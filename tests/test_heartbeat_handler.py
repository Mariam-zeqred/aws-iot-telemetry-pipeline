"""The scheduled heartbeat scan: stale counting, pagination and the EMF line."""

from __future__ import annotations

import json
import time

import boto3
import pytest

from heartbeat import handler as hb
from tests.conftest import REGION


@pytest.fixture
def status(monkeypatch: pytest.MonkeyPatch, tables: dict) -> int:
    monkeypatch.setattr(hb, "STATUS_TABLE", tables["status"])
    monkeypatch.setattr(hb, "STALE_AFTER_S", 600)
    hb.reset_clients()
    now = int(time.time())
    table = boto3.resource("dynamodb", region_name=REGION).Table("status")
    for device_id, age in (("hive-A", 30), ("hive-B", 601), ("hive-C", 7200), ("hive-D", 600)):
        table.put_item(Item={"device_id": device_id, "received_at": now - age, "temp": 32})
    return now


def test_counts_stale_devices_and_emits_one_fleet_line(
    status: int, capsys: pytest.CaptureFixture
) -> None:
    out = hb.handler({})
    assert out["known"] == 4 and out["online"] == 2 and out["stale"] == 2
    assert out["stale_ids"] == ["hive-B", "hive-C"]  # exactly 600 s old is still online

    emf = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert emf["StaleDevices"] == 2 and emf["OnlineDevices"] == 2 and emf["KnownDevices"] == 4
    assert emf["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]  # fleet-wide, no DeviceId
    assert emf["stale_ids"] == ["hive-B", "hive-C"] and emf["stale_ids_truncated"] is False
    names = {m["Name"] for m in emf["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert names == {"StaleDevices", "OnlineDevices", "KnownDevices"}


def test_empty_table_is_all_zero(monkeypatch: pytest.MonkeyPatch, tables: dict) -> None:
    monkeypatch.setattr(hb, "STATUS_TABLE", tables["status"])
    hb.reset_clients()
    out = hb.handler({})
    assert (out["known"], out["online"], out["stale"]) == (0, 0, 0)


def test_scan_follows_pagination() -> None:
    class Pages:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def scan(self, **kwargs: object) -> dict:
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {
                    "Items": [{"device_id": "a", "received_at": 1}],
                    "LastEvaluatedKey": {"k": 1},
                }
            return {"Items": [{"device_id": "b", "received_at": 2}]}

    table = Pages()
    items = list(hb.scan_status(table))
    assert [i["device_id"] for i in items] == ["a", "b"]
    assert len(table.calls) == 2 and table.calls[1]["ExclusiveStartKey"] == {"k": 1}


def test_classify_truncates_logged_ids_but_counts_all() -> None:
    items = [{"device_id": f"hive-{i:03d}", "received_at": 0} for i in range(80)]
    result = hb.classify(iter(items), now=10_000, stale_after_s=600)
    assert result["known"] == 80 and len(result["stale"]) == 80 and result["online"] == 0
    assert hb.MAX_IDS_LOGGED == 50
