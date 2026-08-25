from __future__ import annotations

from app.withings import parse_measure_groups


def test_parse_measure_groups_applies_decimal_exponent() -> None:
    body = {
        "measuregrps": [
            {
                "grpid": 123,
                "date": 1787635800,
                "category": 1,
                "hash_deviceid": "device-hash",
                "measures": [
                    {"value": 72100, "type": 1, "unit": -3},
                    {"value": 201, "type": 6, "unit": -1},
                ],
            }
        ]
    }
    parsed = parse_measure_groups(body)
    assert len(parsed) == 2
    assert parsed[0].metric == "weight"
    assert parsed[0].value == 72.1
    assert parsed[1].metric == "fat_ratio"
    assert parsed[1].value == 20.1


def test_unknown_measure_type_is_preserved() -> None:
    body = {
        "measuregrps": [
            {
                "grpid": 1,
                "date": 1787635800,
                "measures": [{"value": 7, "type": 999, "unit": 0}],
            }
        ]
    }
    parsed = parse_measure_groups(body)
    assert parsed[0].metric == "withings_type_999"
    assert parsed[0].value == 7
