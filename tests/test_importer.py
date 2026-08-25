from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.db import Database
from app.importer import import_file

WEIGHT_CSV = b'''Date,"Weight (kg)","Fat mass (kg)","Bone mass (kg)","Muscle mass (kg)","Hydration (kg)",Comments
"2026-08-24 07:30:00",72.4,15.2,3.1,54.1,40.2,
"2026-08-25 07:31:00",72.1,15.0,3.1,54.0,40.0,
'''


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "withings.db")
    database.initialize()
    return database


def test_import_weight_csv(tmp_path: Path) -> None:
    database = _database(tmp_path)
    report = import_file(
        database,
        data=WEIGHT_CSV,
        filename="weight.csv",
        timezone_name="Europe/Berlin",
    )
    assert report.rows_seen == 2
    assert report.inserted == 10
    assert report.duplicates == 0

    rows = database.query_measurements(metric="weight", limit=10)
    assert len(rows) == 2
    assert rows[0]["value"] == 72.1


def test_reimport_is_deduplicated(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = import_file(
        database,
        data=WEIGHT_CSV,
        filename="weight.csv",
        timezone_name="Europe/Berlin",
    )
    second = import_file(
        database,
        data=WEIGHT_CSV,
        filename="weight.csv",
        timezone_name="Europe/Berlin",
    )
    assert first.inserted == 10
    assert second.inserted == 0
    assert second.duplicates == 10


def test_zip_archive(tmp_path: Path) -> None:
    database = _database(tmp_path)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("export/weight.csv", WEIGHT_CSV)
        archive.writestr("export/readme.txt", "ignored")
    report = import_file(
        database,
        data=archive_bytes.getvalue(),
        filename="withings.zip",
        timezone_name="Europe/Berlin",
    )
    assert report.files_seen == 1
    assert report.inserted == 10


def test_semicolon_decimal_comma_and_pounds(tmp_path: Path) -> None:
    database = _database(tmp_path)
    csv_data = (
        b'Date;Weight (lb);Fat ratio (%)\n'
        b'25.08.2026 08:00;158,733;20,1\n'
    )
    report = import_file(
        database,
        data=csv_data,
        filename="weight.csv",
        timezone_name="Europe/Berlin",
    )
    assert report.inserted == 2
    weight = database.query_measurements(metric="weight", limit=1)[0]
    assert abs(weight["value"] - 72.0) < 0.02
