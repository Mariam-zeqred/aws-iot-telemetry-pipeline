#!/usr/bin/env python3
"""Serve the dashboard locally against a fake status API fed by the simulator model.

No AWS needed: this is for developing `dashboard/index.html` and for the README screenshot.

    python scripts/mock_api.py --port 8765        # then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
sys.path.insert(0, str(ROOT / "simulator"))

from common.telemetry import enrich, validate  # noqa: E402
from incubator_model import IncubatorModel  # noqa: E402


def build_fleet(now: int, hours: float = 1.5) -> dict[str, list[dict[str, Any]]]:
    """Three devices: one steady with a door event, one steady, one that went silent an hour ago."""
    fleet: dict[str, list[dict[str, Any]]] = {}
    specs = [
        ("hive-1C5A00", 32.0, 0, 2),
        ("hive-SIM001", 34.5, 60 * 60, 5),
        ("hive-SIM002", 38.8, 0, 9),
    ]
    for device_id, target, silent_for, seed in specs:
        n = int(hours * 3600 / 15)
        model = IncubatorModel(
            device_id=device_id, target=target, seed=seed, start_ts=now - n * 15 - silent_for
        )
        records = []
        for i in range(n):
            if device_id == "hive-1C5A00" and i == n - 150:
                model.open_door(180)
            rec = model.step()
            if rec["ts"] > now - silent_for:
                break
            full = enrich(
                validate(rec, now=now), device_id=device_id, received_at=rec["ts"] + 1, ttl_days=30
            )
            records.append(full)
        fleet[device_id] = records
    return fleet


class Handler(SimpleHTTPRequestHandler):
    fleet: dict[str, list[dict[str, Any]]] = {}
    stale_after = 600

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT / "dashboard"), **kwargs)

    def _json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("access-control-allow-origin", "*")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _latest(self, device_id: str, now: int) -> dict[str, Any]:
        item = dict(self.fleet[device_id][-1])
        item["age_s"] = now - item["received_at"]
        item["online"] = item["age_s"] <= self.stale_after
        return item

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        now = int(time.time())
        path, _, query = self.path.partition("?")
        if path == "/config.json":
            return self._json(200, {"apiUrl": f"http://127.0.0.1:{self.server.server_port}"})
        if path == "/devices":
            return self._json(200, {"devices": [self._latest(d, now) for d in sorted(self.fleet)]})
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "devices" and parts[1] in self.fleet:
            if len(parts) == 3 and parts[2] == "history":
                minutes = int(
                    dict(p.split("=") for p in query.split("&") if "=" in p).get("minutes", 60)
                )
                items = [r for r in self.fleet[parts[1]] if r["ts"] >= now - minutes * 60]
                return self._json(200, {"device_id": parts[1], "minutes": minutes, "items": items})
            return self._json(200, self._latest(parts[1], now))
        return super().do_GET()

    def log_message(self, *_: Any) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    Handler.fleet = build_fleet(int(time.time()))
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as srv:
        print(f"dashboard with mock API at http://127.0.0.1:{args.port}/", file=sys.stderr)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
