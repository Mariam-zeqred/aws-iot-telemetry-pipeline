"""The telemetry record: one schema shared by the ingest Lambda, the status API and the simulator.

The field names are the ones the queen-bee incubator firmware already writes to Firebase
(see incubator-telemetry-analysis/data/hive-*_history_*.json), so a device does not have
to change its payload to move from Firebase RTDB to this pipeline.

This module has no AWS dependency on purpose: it is unit-tested on its own and copied into
the Lambda deployment package as-is.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

TOPIC_PATTERN = re.compile(r"^incubator/(?P<device_id>[A-Za-z0-9_-]{1,64})/telemetry$")

# Field -> python type the value must be coercible to.
REQUIRED_FIELDS: dict[str, type] = {
    "ts": int,  # unix seconds, device clock
    "temp": float,  # chamber temperature, deg C
    "hum": float,  # chamber relative humidity, %
    "targetTemp": float,  # setpoint, deg C
    "heater": bool,  # relay state
}
OPTIONAL_FIELDS: dict[str, type] = {
    "tempOut": float,
    "humOut": float,
    "door": str,  # "shut" | "open"
    "cycleDay": int,
    "cycleCode": str,
    "elapsedSec": int,
    "iso": str,  # device-formatted timestamp, kept for humans, never trusted
}
RANGES: dict[str, tuple[float, float]] = {
    "temp": (-20.0, 80.0),
    "hum": (0.0, 100.0),
    "tempOut": (-40.0, 80.0),
    "humOut": (0.0, 100.0),
    "targetTemp": (20.0, 45.0),
}
DOOR_VALUES = frozenset({"shut", "open"})
MAX_PAST_S = 7 * 24 * 3600  # a device may buffer a week offline and backfill
MAX_FUTURE_S = 5 * 60  # clock skew we tolerate


class ValidationError(ValueError):
    """Raised with a short machine-readable reason, e.g. "missing:temp" or "range:hum"."""


def parse_topic(topic: str) -> str:
    """Return the device id embedded in an `incubator/<id>/telemetry` topic."""
    m = TOPIC_PATTERN.match(topic)
    if not m:
        raise ValidationError(f"topic:{topic}")
    return m.group("device_id")


def _coerce(name: str, value: Any, kind: type) -> Any:
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.lower() in ("true", "false", "0", "1"):
            return value.lower() in ("true", "1")
        raise ValidationError(f"type:{name}")
    if kind is int:
        if isinstance(value, bool):
            raise ValidationError(f"type:{name}")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
        raise ValidationError(f"type:{name}")
    if kind is float:
        if isinstance(value, bool):
            raise ValidationError(f"type:{name}")
        try:
            out = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"type:{name}") from exc
        if out != out or out in (float("inf"), float("-inf")):
            raise ValidationError(f"type:{name}")
        return out
    if kind is str:
        if not isinstance(value, str) or len(value) > 64:
            raise ValidationError(f"type:{name}")
        return value
    raise TypeError(kind)


def validate(payload: Any, *, now: int) -> dict[str, Any]:
    """Return a clean record built from `payload`, or raise ValidationError.

    Unknown fields are dropped (a firmware update must not be able to grow the table
    silently). Numbers are coerced from strings because some MQTT libraries send them so.
    """
    if not isinstance(payload, dict):
        raise ValidationError("type:payload")
    record: dict[str, Any] = {}
    for name, kind in REQUIRED_FIELDS.items():
        if name not in payload or payload[name] is None:
            raise ValidationError(f"missing:{name}")
        record[name] = _coerce(name, payload[name], kind)
    for name, kind in OPTIONAL_FIELDS.items():
        if name in payload and payload[name] is not None:
            record[name] = _coerce(name, payload[name], kind)
    for name, (lo, hi) in RANGES.items():
        if name in record and not lo <= record[name] <= hi:
            raise ValidationError(f"range:{name}")
    if "door" in record and record["door"] not in DOOR_VALUES:
        raise ValidationError("range:door")
    if record["ts"] < now - MAX_PAST_S or record["ts"] > now + MAX_FUTURE_S:
        raise ValidationError("range:ts")
    return record


def enrich(
    record: dict[str, Any], *, device_id: str, received_at: int, ttl_days: int
) -> dict[str, Any]:
    """Add the server-side fields. `received_at` is unix seconds from the IoT rule."""
    out = dict(record)
    out["device_id"] = device_id
    out["received_at"] = received_at
    out["latency_s"] = received_at - record["ts"]
    if "tempOut" in record:
        out["delta_in_out"] = round(record["temp"] - record["tempOut"], 3)
    out["expires_at"] = record["ts"] + ttl_days * 24 * 3600
    out["day"] = datetime.fromtimestamp(record["ts"], tz=UTC).strftime("%Y-%m-%d")
    out["schema_version"] = SCHEMA_VERSION
    return out
