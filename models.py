from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

MEASURE_TYPES: dict[int, tuple[str, str]] = {
    1: ("weight", "kg"),
    4: ("height", "m"),
    5: ("fat_free_mass", "kg"),
    6: ("fat_ratio", "%"),
    8: ("fat_mass", "kg"),
    9: ("diastolic_blood_pressure", "mmHg"),
    10: ("systolic_blood_pressure", "mmHg"),
    11: ("heart_rate", "bpm"),
    12: ("temperature", "°C"),
    54: ("spo2", "%"),
    71: ("body_temperature", "°C"),
    73: ("skin_temperature", "°C"),
    76: ("muscle_mass", "kg"),
    77: ("hydration", "kg"),
    88: ("bone_mass", "kg"),
    91: ("pulse_wave_velocity", "m/s"),
    123: ("vo2_max", "ml/min/kg"),
    130: ("atrial_fibrillation", "result"),
    135: ("qrs_interval", "ms"),
    136: ("pr_interval", "ms"),
    137: ("qt_interval", "ms"),
    138: ("corrected_qt_interval", "ms"),
    139: ("atrial_fibrillation_ppg", "result"),
    155: ("vascular_age", "years"),
    167: ("nerve_health_score", "score"),
    168: ("extracellular_water", "kg"),
    169: ("intracellular_water", "kg"),
    170: ("visceral_fat", "score"),
    173: ("segmental_fat_free_mass", "kg"),
    174: ("segmental_fat_mass", "kg"),
    175: ("segmental_muscle_mass", "kg"),
    196: ("nerve_response_score", "score"),
    226: ("basal_metabolic_rate", "kcal/day"),
    227: ("metabolic_age", "years"),
    229: ("electrochemical_skin_conductance", "µS"),
}


@dataclass(frozen=True)
class CanonicalMeasurement:
    measured_at: int
    metric: str
    value: float
    unit: str
    source: str
    measure_type: int | None = None
    period_end: int | None = None
    external_group_id: str | None = None
    device_id: str | None = None
    category: int | None = None
    raw_json: str | None = None

    @property
    def dedupe_key(self) -> str:
        stable = (
            f"{self.measured_at}|{self.period_end or ''}|{self.metric}|"
            f"{self.value:.8f}|{self.unit}"
        )
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def as_api_dict(self, row_id: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "timestamp": self.measured_at,
            "measured_at": datetime.fromtimestamp(
                self.measured_at, timezone.utc
            ).isoformat(),
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "measure_type": self.measure_type,
            "device_id": self.device_id,
            "category": self.category,
        }
        if row_id is not None:
            result["id"] = row_id
        if self.period_end is not None:
            result["period_end_timestamp"] = self.period_end
            result["period_end"] = datetime.fromtimestamp(
                self.period_end, timezone.utc
            ).isoformat()
        return result


def api_measurement_from_group(group: dict[str, Any], measure: dict[str, Any]) -> CanonicalMeasurement:
    measure_type = int(measure["type"])
    metric, unit = MEASURE_TYPES.get(measure_type, (f"withings_type_{measure_type}", "unknown"))
    value = float(Decimal(str(measure["value"])) * (Decimal(10) ** int(measure.get("unit", 0))))
    group_id = str(group.get("grpid", "")) or None
    device_id = group.get("hash_deviceid") or group.get("deviceid")
    return CanonicalMeasurement(
        measured_at=int(group["date"]),
        metric=metric,
        value=value,
        unit=unit,
        source="withings_api",
        measure_type=measure_type,
        external_group_id=group_id,
        device_id=str(device_id) if device_id else None,
        category=int(group["category"]) if group.get("category") is not None else None,
    )
