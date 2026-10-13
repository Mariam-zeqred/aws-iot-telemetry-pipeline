"""Status API Lambda behind an HTTP API (API Gateway v2).

Routes:
    GET /health
    GET /devices                      -> every device's latest record (status table scan)
    GET /devices/{device_id}          -> that device's latest record
    GET /devices/{device_id}/history  -> ?minutes=60 (max 1440), ascending by ts

Read-only; the dashboard is the only intended client, so CORS allows any origin for GET.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from common.dynamo import from_item

HISTORY_TABLE = os.environ.get("HISTORY_TABLE", "")
STATUS_TABLE = os.environ.get("STATUS_TABLE", "")
STALE_AFTER_S = int(os.environ.get("STALE_AFTER_S", "600"))
MAX_HISTORY_MINUTES = 24 * 60
MAX_HISTORY_ITEMS = 2000

_dynamo = None


def _table(name: str) -> Any:
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(name)


def reset_clients() -> None:
    global _dynamo
    _dynamo = None


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "cache-control": "no-store",
        },
        "body": json.dumps(body),
    }


def _with_staleness(item: dict[str, Any], now: int) -> dict[str, Any]:
    out = from_item(item)
    age = now - int(out.get("received_at", 0))
    out["age_s"] = age
    out["online"] = age <= STALE_AFTER_S
    return out


def list_devices(now: int) -> list[dict[str, Any]]:
    resp = _table(STATUS_TABLE).scan(Limit=200)
    items = [_with_staleness(i, now) for i in resp.get("Items", [])]
    return sorted(items, key=lambda d: d["device_id"])


def get_device(device_id: str, now: int) -> dict[str, Any] | None:
    resp = _table(STATUS_TABLE).get_item(Key={"device_id": device_id})
    item = resp.get("Item")
    return _with_staleness(item, now) if item else None


def get_history(device_id: str, minutes: int, now: int) -> list[dict[str, Any]]:
    minutes = max(1, min(minutes, MAX_HISTORY_MINUTES))
    resp = _table(HISTORY_TABLE).query(
        KeyConditionExpression=Key("device_id").eq(device_id)
        & Key("ts").between(now - minutes * 60, now + 300),
        Limit=MAX_HISTORY_ITEMS,
        ScanIndexForward=True,
    )
    return [from_item(i) for i in resp.get("Items", [])]


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    now = int(time.time())
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}

    if method != "GET":
        return _response(405, {"error": "method not allowed"})
    if path == "/health":
        return _response(200, {"ok": True, "time": now})
    if path == "/devices":
        return _response(200, {"devices": list_devices(now)})
    device_id = params.get("device_id")
    if device_id and path.endswith("/history"):
        try:
            minutes = int(query.get("minutes", "60"))
        except ValueError:
            return _response(400, {"error": "minutes must be an integer"})
        return _response(
            200,
            {
                "device_id": device_id,
                "minutes": minutes,
                "items": get_history(device_id, minutes, now),
            },
        )
    if device_id:
        item = get_device(device_id, now)
        if item is None:
            return _response(404, {"error": f"unknown device {device_id}"})
        return _response(200, item)
    return _response(404, {"error": "no such route"})
