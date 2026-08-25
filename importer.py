from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from app.db import Database
from app.models import CanonicalMeasurement

MAX_ARCHIVE_FILES = 100
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    measure_type: int | None
    canonical_unit: str
    dimension: str = "identity"


@dataclass
class ImportReport:
    filename: str
    files_seen: int = 0
    rows_seen: int = 0
    values_seen: int = 0
    inserted: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    ignored_columns: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "files_seen": self.files_seen,
            "rows_seen": self.rows_seen,
            "values_seen": self.values_seen,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "error_count": len(self.errors),
            "errors": self.errors[:25],
            "ignored_columns": sorted(self.ignored_columns),
        }


KNOWN_METRICS: list[tuple[tuple[str, ...], MetricSpec]] = [
    (("fat free mass", "fettfreie masse"), MetricSpec("fat_free_mass", 5, "kg", "mass")),
    (("fat ratio", "body fat", "körperfett", "koerperfett"), MetricSpec("fat_ratio", 6, "%", "ratio")),
    (("fat mass", "fettmasse"), MetricSpec("fat_mass", 8, "kg", "mass")),
    (("muscle mass", "muskelmasse"), MetricSpec("muscle_mass", 76, "kg", "mass")),
    (("bone mass", "knochenmasse"), MetricSpec("bone_mass", 88, "kg", "mass")),
    (("hydration", "körperwasser", "koerperwasser", "wasser"), MetricSpec("hydration", 77, "kg", "mass")),
    (("systolic", "systole"), MetricSpec("systolic_blood_pressure", 10, "mmHg")),
    (("diastolic", "diastole"), MetricSpec("diastolic_blood_pressure", 9, "mmHg")),
    (("heart rate", "pulse", "puls", "herzfrequenz"), MetricSpec("heart_rate", 11, "bpm")),
    (("spo2", "oxygen saturation", "sauerstoffsättigung", "sauerstoffsaettigung"), MetricSpec("spo2", 54, "%", "ratio")),
    (("body temperature", "körpertemperatur", "koerpertemperatur"), MetricSpec("body_temperature", 71, "°C")),
    (("skin temperature", "hauttemperatur"), MetricSpec("skin_temperature", 73, "°C")),
    (("temperature", "temperatur"), MetricSpec("temperature", 12, "°C")),
    (("height", "größe", "groesse"), MetricSpec("height", 4, "m", "length")),
    (("weight", "gewicht"), MetricSpec("weight", 1, "kg", "mass")),
    (("active calories", "aktive kalorien"), MetricSpec("active_calories", None, "kcal")),
    (("calories", "kalorien"), MetricSpec("calories", None, "kcal")),
    (("steps", "schritte"), MetricSpec("steps", None, "count")),
    (("distance", "entfernung", "strecke"), MetricSpec("distance", None, "km", "distance")),
    (("elevation", "höhenmeter", "hoehenmeter"), MetricSpec("elevation", None, "m", "length")),
    (("light sleep", "leichter schlaf"), MetricSpec("light_sleep", None, "s", "duration")),
    (("deep sleep", "tiefschlaf"), MetricSpec("deep_sleep", None, "s", "duration")),
    (("rem sleep", "rem"), MetricSpec("rem_sleep", None, "s", "duration")),
    (("awake time", "wachzeit"), MetricSpec("awake_time", None, "s", "duration")),
    (("wake up", "aufwachen"), MetricSpec("wake_ups", None, "count")),
]

DATE_HEADERS = {"date", "datum", "datetime", "timestamp", "start", "from", "von"}
END_HEADERS = {"end", "to", "bis", "period end"}
IGNORED_HEADERS = {"comments", "comment", "kommentar", "notes", "note", "timezone"}


def _normalized(value: str) -> str:
    value = value.strip().lower().replace("_", " ")
    value = re.sub(r"\([^)]*\)", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_unit(header: str) -> str:
    match = re.search(r"\(([^)]+)\)", header)
    return match.group(1).strip().lower() if match else ""


def _metric_for_header(header: str) -> MetricSpec | None:
    normalized = _normalized(header)
    for aliases, spec in KNOWN_METRICS:
        if any(alias in normalized for alias in aliases):
            return spec
    return None


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Zeichencodierung der CSV-Datei wird nicht unterstützt")


def _dialect(text: str) -> csv.Dialect:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _number(value: str, delimiter: str) -> float:
    cleaned = value.strip().replace("\u00a0", "").replace(" ", "")
    if delimiter == ";" and "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def _timestamp(value: str, local_zone: ZoneInfo) -> int:
    raw = value.strip().strip('"')
    if not raw:
        raise ValueError("leeres Datum")
    if raw.isdigit() and len(raw) >= 9:
        return int(raw)

    parsed: datetime | None = None
    iso_value = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        pass
    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%Y-%m-%d",
            "%d.%m.%Y",
        ):
            try:
                parsed = datetime.strptime(raw, pattern).replace(tzinfo=local_zone)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"unbekanntes Datumsformat: {raw}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_zone)
    return int(parsed.astimezone(timezone.utc).timestamp())


def _convert(value: float, header_unit: str, spec: MetricSpec) -> tuple[float, str]:
    unit = header_unit.replace("°", "").lower()
    if spec.dimension == "mass":
        if unit in {"lb", "lbs", "pound", "pounds"}:
            return value * 0.45359237, "kg"
        if unit in {"g", "gram", "grams"}:
            return value / 1000.0, "kg"
        return value, "kg"
    if spec.dimension == "length":
        if spec.metric == "height" and unit in {"cm", "centimeter", "zentimeter"}:
            return value / 100.0, "m"
        if unit in {"ft", "feet"}:
            return value * 0.3048, "m"
        return value, spec.canonical_unit
    if spec.dimension == "distance":
        if unit in {"m", "meter", "metre"}:
            return value / 1000.0, "km"
        if unit in {"mi", "mile", "miles"}:
            return value * 1.609344, "km"
        return value, "km"
    if spec.dimension == "duration":
        if unit in {"min", "minute", "minutes"}:
            return value * 60.0, "s"
        if unit in {"h", "hr", "hour", "hours"}:
            return value * 3600.0, "s"
        return value, "s"
    return value, spec.canonical_unit


def parse_csv_measurements(
    data: bytes,
    *,
    filename: str,
    timezone_name: str,
    report: ImportReport,
) -> list[CanonicalMeasurement]:
    text = _decode(data)
    dialect = _dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV-Datei hat keine Kopfzeile")

    date_header = next(
        (header for header in reader.fieldnames if _normalized(header) in DATE_HEADERS),
        None,
    )
    end_header = next(
        (header for header in reader.fieldnames if _normalized(header) in END_HEADERS),
        None,
    )
    if date_header is None:
        raise ValueError("Keine Datumsspalte gefunden")

    metric_headers: dict[str, MetricSpec] = {}
    for header in reader.fieldnames:
        if header in {date_header, end_header} or _normalized(header) in IGNORED_HEADERS:
            continue
        spec = _metric_for_header(header)
        if spec:
            metric_headers[header] = spec
        else:
            report.ignored_columns.add(f"{filename}: {header}")

    if not metric_headers:
        raise ValueError("Keine unterstützte Messwertspalte gefunden")

    local_zone = ZoneInfo(timezone_name)
    measurements: list[CanonicalMeasurement] = []
    for row_number, row in enumerate(reader, start=2):
        report.rows_seen += 1
        try:
            measured_at = _timestamp(row.get(date_header, ""), local_zone)
            period_end = (
                _timestamp(row.get(end_header, ""), local_zone)
                if end_header and row.get(end_header, "").strip()
                else None
            )
        except (ValueError, TypeError) as exc:
            report.errors.append(f"{filename}, Zeile {row_number}: {exc}")
            continue

        for header, spec in metric_headers.items():
            raw_value = (row.get(header) or "").strip()
            if not raw_value:
                continue
            report.values_seen += 1
            try:
                value = _number(raw_value, getattr(dialect, "delimiter", ","))
                converted, unit = _convert(value, _extract_unit(header), spec)
            except ValueError:
                report.errors.append(
                    f"{filename}, Zeile {row_number}, Spalte {header}: keine Zahl"
                )
                continue
            measurements.append(
                CanonicalMeasurement(
                    measured_at=measured_at,
                    period_end=period_end,
                    metric=spec.metric,
                    measure_type=spec.measure_type,
                    value=converted,
                    unit=unit,
                    source="withings_export",
                    raw_json=json.dumps(
                        {"file": filename, "row": row_number, "header": header},
                        ensure_ascii=False,
                    ),
                )
            )
    return measurements


def _archive_csv_files(data: bytes, filename: str) -> Iterable[tuple[str, bytes]]:
    if filename.lower().endswith(".zip") or data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > MAX_ARCHIVE_FILES:
                raise ValueError(f"Archiv enthält mehr als {MAX_ARCHIVE_FILES} Dateien")
            total = sum(item.file_size for item in members)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Entpacktes Archiv ist größer als 100 MiB")
            for item in members:
                safe_name = PurePosixPath(item.filename).name
                if safe_name.lower().endswith(".csv"):
                    yield safe_name, archive.read(item)
    else:
        yield PurePosixPath(filename).name, data


def import_file(
    database: Database,
    *,
    data: bytes,
    filename: str,
    timezone_name: str,
) -> ImportReport:
    report = ImportReport(filename=filename)
    csv_found = False
    for csv_name, csv_data in _archive_csv_files(data, filename):
        csv_found = True
        report.files_seen += 1
        try:
            measurements = parse_csv_measurements(
                csv_data,
                filename=csv_name,
                timezone_name=timezone_name,
                report=report,
            )
        except (ValueError, csv.Error) as exc:
            report.errors.append(f"{csv_name}: {exc}")
            continue
        inserted, duplicates = database.upsert_measurements(measurements)
        report.inserted += inserted
        report.duplicates += duplicates

    if not csv_found:
        raise ValueError("Keine CSV-Datei im Upload gefunden")

    database.record_import(
        filename=filename,
        rows_seen=report.rows_seen,
        inserted=report.inserted,
        duplicates=report.duplicates,
        errors=len(report.errors),
        details=report.as_dict(),
    )
    return report
