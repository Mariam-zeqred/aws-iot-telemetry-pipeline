"""Heartbeat Lambda: one scheduled scan of the status table instead of one alarm per device.

Runs every `heartbeat_period_minutes` (EventBridge schedule, see src/pipeline/monitoring.py).
It reads every device's latest `received_at` from the status table, counts the devices whose
last message is older than STALE_AFTER_S, and prints one EMF line with three fleet metrics:

    StaleDevices   devices silent for longer than STALE_AFTER_S
    OnlineDevices  the rest
    KnownDevices   items in the status table

The stale ids ride along as a plain property of the same log line (not a metric dimension),
so CloudWatch Logs Insights can list them while the metric count stays at three for any fleet
size. One alarm on StaleDevices replaces N per-device alarms; see scripts/cost.py for what
that saves at 1 000 devices.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import boto3

from common import metrics

STATUS_TABLE = os.environ.get("STATUS_TABLE", "")
STALE_AFTER_S = int(os.environ.get("STALE_AFTER_S", "600"))
NAMESPACE = os.environ.get("METRIC_NAMESPACE", "Incubator/Telemetry")
MAX_IDS_LOGGED = 50

_dynamo = None


def _table() -> Any:
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(STATUS_TABLE)


def reset_clients() -> None:
    """For tests: forget the cached resource so moto's fake is picked up."""
    global _dynamo
    _dynamo = None


def scan_status(table: Any) -> Iterator[dict[str, Any]]:
    """Every item of the status table, following pagination (1 MB per page)."""
    kwargs: dict[str, Any] = {"ProjectionExpression": "device_id, received_at"}
    while True:
        page = table.scan(**kwargs)
        yield from page.get("Items", [])
        key = page.get("LastEvaluatedKey")
        if not key:
            return
        kwargs["ExclusiveStartKey"] = key


def classify(items: Iterator[dict[str, Any]], *, now: int, stale_after_s: int) -> dict[str, Any]:
    """Split devices into online and stale by the age of their last accepted message."""
    stale: list[str] = []
    known = 0
    for item in items:
        known += 1
        age = now - int(item.get("received_at", 0))
        if age > stale_after_s:
            stale.append(str(item["device_id"]))
    stale.sort()
    return {"known": known, "stale": stale, "online": known - len(stale)}


def handler(_event: dict[str, Any] | None = None, _context: Any = None) -> dict[str, Any]:
    now = int(time.time())
    result = classify(scan_status(_table()), now=now, stale_after_s=STALE_AFTER_S)
    metrics.emit(
        NAMESPACE,
        {
            "StaleDevices": len(result["stale"]),
            "OnlineDevices": result["online"],
            "KnownDevices": result["known"],
        },
        {},
        stale_after_s=STALE_AFTER_S,
        stale_ids=result["stale"][:MAX_IDS_LOGGED],
        stale_ids_truncated=len(result["stale"]) > MAX_IDS_LOGGED,
    )
    return {
        "known": result["known"],
        "online": result["online"],
        "stale": len(result["stale"]),
        "stale_ids": result["stale"][:MAX_IDS_LOGGED],
        "stale_after_s": STALE_AFTER_S,
        "now": now,
    }
