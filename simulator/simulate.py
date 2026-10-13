#!/usr/bin/env python3
"""Publish incubator telemetry to AWS IoT Core over MQTT with X.509 client certificates.

Two sources: the thermal model (default) or a replay of a real history export
(`--replay simulator/sample_history.json`, timestamps shifted to now). `--dry-run` prints
the JSON payloads instead of connecting, which is what the tests and CI use.

    python simulator/simulate.py --dry-run --count 5
    python simulator/simulate.py --endpoint xxx-ats.iot.eu-central-1.amazonaws.com \
        --device-id hive-SIM001 --cert certs/hive-SIM001.pem.crt \
        --key certs/hive-SIM001.private.key --ca certs/AmazonRootCA1.pem
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from incubator_model import IncubatorModel  # noqa: E402

TOPIC_PREFIX = "incubator"


def model_records(args: argparse.Namespace) -> Iterator[dict[str, Any]]:
    model = IncubatorModel(
        device_id=args.device_id,
        target=args.target,
        ambient=args.ambient,
        interval_s=args.interval,
        start_ts=int(time.time()),
        seed=args.seed,
    )
    n = 0
    while args.count <= 0 or n < args.count:
        if args.door_every and n and n % args.door_every == 0:
            model.open_door(120)
        yield model.step()
        n += 1


def replay_records(path: Path, count: int, shift_to_now: bool = True) -> Iterator[dict[str, Any]]:
    rows = json.loads(path.read_text())
    if not rows:
        return
    offset = int(time.time()) - rows[-1]["ts"] if shift_to_now else 0
    for i, row in enumerate(rows):
        if 0 < count <= i:
            break
        rec = dict(row)
        rec["ts"] = row["ts"] + offset
        rec.pop("iso", None)  # the device-side string would now be wrong; drop it
        yield rec


def records(args: argparse.Namespace) -> Iterator[dict[str, Any]]:
    if args.replay:
        return replay_records(Path(args.replay), args.count)
    return model_records(args)


def topic_for(device_id: str) -> str:
    return f"{TOPIC_PREFIX}/{device_id}/telemetry"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--endpoint", default=os.environ.get("AWS_IOT_ENDPOINT", ""))
    p.add_argument("--port", type=int, default=8883)
    p.add_argument("--device-id", default=os.environ.get("DEVICE_ID", "hive-SIM001"))
    p.add_argument("--cert", default=os.environ.get("DEVICE_CERT", ""))
    p.add_argument("--key", default=os.environ.get("DEVICE_KEY", ""))
    p.add_argument("--ca", default=os.environ.get("ROOT_CA", "simulator/certs/AmazonRootCA1.pem"))
    p.add_argument("--interval", type=int, default=15, help="seconds between messages")
    p.add_argument("--count", type=int, default=0, help="messages to send; 0 = forever")
    p.add_argument(
        "--replay", default="", help="JSON history export to replay instead of the model"
    )
    p.add_argument("--target", type=float, default=34.5)
    p.add_argument("--ambient", type=float, default=27.0)
    p.add_argument("--door-every", type=int, default=0, help="open the door for 2 min every N msgs")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--dry-run", action="store_true", help="print payloads, do not connect")
    p.add_argument("--no-sleep", action="store_true", help="do not wait between messages")
    return p


def publish_loop(args: argparse.Namespace, out: Any = sys.stdout) -> int:
    client = None
    if not args.dry_run:
        for name in ("endpoint", "cert", "key", "ca"):
            if not getattr(args, name):
                print(f"--{name} is required unless --dry-run", file=sys.stderr)
                return 2
        import paho.mqtt.client as mqtt  # imported late so --dry-run has no dependency

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.device_id)
        client.tls_set(
            ca_certs=args.ca, certfile=args.cert, keyfile=args.key, tls_version=ssl.PROTOCOL_TLSv1_2
        )
        client.connect(args.endpoint, args.port, keepalive=60)
        client.loop_start()

    sent = 0
    topic = topic_for(args.device_id)
    try:
        for rec in records(args):
            payload = json.dumps(rec, separators=(",", ":"))
            if client is None:
                print(payload, file=out)
            else:
                info = client.publish(topic, payload, qos=1)
                info.wait_for_publish(timeout=10)
            sent += 1
            if not args.no_sleep and not args.dry_run:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()
    print(f"sent {sent} messages to {topic}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return publish_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
