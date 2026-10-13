import json
import time

import boto3
import pytest

from common.dynamo import to_item
from common.telemetry import enrich, validate
from status_api import handler as api
from tests.conftest import REGION
from tests.test_telemetry import GOOD


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch, tables: dict) -> int:
    monkeypatch.setattr(api, "HISTORY_TABLE", tables["history"])
    monkeypatch.setattr(api, "STATUS_TABLE", tables["status"])
    api.reset_clients()
    now = int(time.time())
    ddb = boto3.resource("dynamodb", region_name=REGION)
    for dev, offset in (("hive-A", 30), ("hive-B", 2000)):
        for k in range(5):
            ts = now - offset - k * 15
            rec = validate(dict(GOOD, ts=ts, temp=30 + k), now=now)
            full = enrich(rec, device_id=dev, received_at=ts + 1, ttl_days=30)
            ddb.Table("history").put_item(Item=to_item(full))
            if k == 0:
                ddb.Table("status").put_item(Item=to_item(full))
    return now


def _get(path: str, **query: str) -> dict:
    parts = path.strip("/").split("/")
    params = {"device_id": parts[1]} if len(parts) >= 2 and parts[0] == "devices" else {}
    ev = {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET"}},
        "pathParameters": params,
        "queryStringParameters": query or None,
    }
    resp = api.handler(ev)
    return {
        "status": resp["statusCode"],
        "body": json.loads(resp["body"]),
        "headers": resp["headers"],
    }


def test_health_and_cors(seeded: int) -> None:
    r = _get("/health")
    assert r["status"] == 200 and r["body"]["ok"] is True
    assert r["headers"]["access-control-allow-origin"] == "*"


def test_list_devices_marks_staleness(seeded: int) -> None:
    r = _get("/devices")
    devs = {d["device_id"]: d for d in r["body"]["devices"]}
    assert set(devs) == {"hive-A", "hive-B"}
    assert devs["hive-A"]["online"] is True
    assert devs["hive-B"]["online"] is False and devs["hive-B"]["age_s"] >= 1990
    assert devs["hive-A"]["hum"] == 81.5  # Decimal converted back to a float


def test_get_device_and_404(seeded: int) -> None:
    assert _get("/devices/hive-A")["body"]["temp"] == 30
    assert _get("/devices/hive-Z")["status"] == 404


def test_history_window_and_order(seeded: int) -> None:
    r = _get("/devices/hive-A/history", minutes="60")
    ts = [i["ts"] for i in r["body"]["items"]]
    assert len(ts) == 5 and ts == sorted(ts)
    r = _get("/devices/hive-B/history", minutes="10")
    assert r["body"]["items"] == []
    assert _get("/devices/hive-A/history", minutes="x")["status"] == 400


def test_post_rejected(seeded: int) -> None:
    ev = {"rawPath": "/devices", "requestContext": {"http": {"method": "POST"}}}
    assert api.handler(ev)["statusCode"] == 405
