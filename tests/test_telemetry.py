import pytest

from common.telemetry import (
    SCHEMA_VERSION,
    ValidationError,
    enrich,
    parse_topic,
    validate,
)

NOW = 1_791_021_800
GOOD = {
    "cycleCode": "3D58E4D9",
    "cycleDay": 1,
    "door": "shut",
    "elapsedSec": 151796,
    "heater": False,
    "hum": 81.5,
    "humOut": 52.9,
    "iso": "2026-10-03 13:01:51",
    "targetTemp": 32.0,
    "temp": 26.87,
    "tempOut": 27.25,
    "ts": 1791021711,
}


def test_real_record_validates() -> None:
    rec = validate(GOOD, now=NOW)
    assert rec == GOOD


def test_unknown_fields_dropped_and_strings_coerced() -> None:
    payload = dict(GOOD, temp="30.5", heater="true", cycleDay="2", extra="x")
    rec = validate(payload, now=NOW)
    assert rec["temp"] == 30.5 and rec["heater"] is True and rec["cycleDay"] == 2
    assert "extra" not in rec


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"temp": None}, "missing:temp"),
        ({"temp": "hot"}, "type:temp"),
        ({"temp": float("nan")}, "type:temp"),
        ({"hum": 101}, "range:hum"),
        ({"targetTemp": 60}, "range:targetTemp"),
        ({"door": "ajar"}, "range:door"),
        ({"heater": "maybe"}, "type:heater"),
        ({"ts": True}, "type:ts"),
        ({"ts": NOW + 3600}, "range:ts"),
        ({"ts": NOW - 8 * 86400}, "range:ts"),
        ({"cycleCode": "x" * 65}, "type:cycleCode"),
    ],
)
def test_rejections(change: dict, reason: str) -> None:
    payload = dict(GOOD, **change)
    with pytest.raises(ValidationError, match=reason):
        validate(payload, now=NOW)


def test_minimal_record() -> None:
    rec = validate({"ts": NOW, "temp": 30, "hum": 70, "targetTemp": 34.5, "heater": 1}, now=NOW)
    assert rec["heater"] is True and "tempOut" not in rec


def test_non_dict_payload() -> None:
    with pytest.raises(ValidationError, match="type:payload"):
        validate([1, 2], now=NOW)


def test_enrich_adds_server_fields() -> None:
    rec = validate(GOOD, now=NOW)
    full = enrich(rec, device_id="hive-1C5A00", received_at=NOW, ttl_days=30)
    assert full["device_id"] == "hive-1C5A00"
    assert full["latency_s"] == NOW - GOOD["ts"]
    assert full["delta_in_out"] == pytest.approx(26.87 - 27.25, abs=1e-3)
    assert full["expires_at"] == GOOD["ts"] + 30 * 86400
    assert full["day"] == "2026-10-03"
    assert full["schema_version"] == SCHEMA_VERSION
    assert rec == GOOD, "enrich must not mutate its input"


def test_parse_topic() -> None:
    assert parse_topic("incubator/hive-1C5A00/telemetry") == "hive-1C5A00"
    for bad in (
        "incubator/telemetry",
        "incubator/a/b/telemetry",
        "other/x/telemetry",
        "incubator/a b/telemetry",
    ):
        with pytest.raises(ValidationError):
            parse_topic(bad)
