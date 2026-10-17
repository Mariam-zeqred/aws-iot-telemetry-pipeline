"""scripts/mock_api.py: the local stand-in for the status API behind the dashboard screenshot."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

import mock_api


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    now = int(time.time())
    mock_api.Handler.fleet = mock_api.build_fleet(now, hours=0.5)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mock_api.Handler)  # port 0: any free port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str) -> tuple[int, str, bytes]:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (local test server)
        return resp.status, resp.headers.get("content-type", ""), resp.read()


def test_build_fleet_shapes_and_silence() -> None:
    now = 1_791_021_800
    fleet = mock_api.build_fleet(now, hours=0.5)
    assert set(fleet) == {"hive-1C5A00", "hive-SIM001", "hive-SIM002"}
    for records in fleet.values():
        assert records and all(r["device_id"] for r in records)
        assert all(r["schema_version"] for r in records)  # enriched, validated records
        assert [r["ts"] for r in records] == sorted(r["ts"] for r in records)
    assert now - fleet["hive-SIM001"][-1]["received_at"] >= 3500  # went silent an hour ago
    assert now - fleet["hive-1C5A00"][-1]["received_at"] < 60


def test_devices_route_marks_the_silent_device_offline(server: str) -> None:
    status, ctype, body = _get(f"{server}/devices")
    assert status == 200 and ctype == "application/json"
    devices = {d["device_id"]: d for d in json.loads(body)["devices"]}
    assert list(devices) == sorted(devices)
    assert devices["hive-1C5A00"]["online"] is True
    assert devices["hive-SIM001"]["online"] is False and devices["hive-SIM001"]["age_s"] >= 3500


def test_single_device_and_history_window(server: str) -> None:
    _, _, body = _get(f"{server}/devices/hive-SIM002")
    assert json.loads(body)["targetTemp"] == 38.8
    _, _, body = _get(f"{server}/devices/hive-1C5A00/history?minutes=10")
    items = json.loads(body)["items"]
    assert 30 <= len(items) <= 41  # ten minutes at 15 s
    assert items[-1]["ts"] - items[0]["ts"] <= 600


def test_static_page_and_config(server: str) -> None:
    status, ctype, body = _get(f"{server}/")
    assert status == 200 and "text/html" in ctype and b"<html" in body.lower()
    _, _, body = _get(f"{server}/config.json")
    assert json.loads(body)["apiUrl"] == server
