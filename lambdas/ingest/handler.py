"""Ingest Lambda: one invocation per MQTT message, called by the IoT topic rule.

Event shape (set by the rule's SQL in src/pipeline/ingest.py):
    SELECT *, topic(2) AS device_id, timestamp() AS received_at_ms
    FROM 'incubator/+/telemetry'

Accepted messages go to three places:
  1. the history table   (hot, 30-day TTL, keyed device_id + ts),
  2. the status table    (one item per device, overwritten: "latest"),
  3. Firehose            (batched to S3 as Parquet for Athena, kept for years).
Rejected messages go to an SQS queue with the reason, so a bad firmware build is visible
instead of silently dropped. Both paths print an EMF metric line.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3

from common import metrics
from common.dynamo import to_item
from common.telemetry import ValidationError, enrich, validate

HISTORY_TABLE = os.environ.get("HISTORY_TABLE", "")
STATUS_TABLE = os.environ.get("STATUS_TABLE", "")
FIREHOSE_STREAM = os.environ.get("FIREHOSE_STREAM", "")
REJECT_QUEUE_URL = os.environ.get("REJECT_QUEUE_URL", "")
TTL_DAYS = int(os.environ.get("TTL_DAYS", "30"))
NAMESPACE = os.environ.get("METRIC_NAMESPACE", "Incubator/Telemetry")
# "1": Accepted/Rejected also get a DeviceId dimension (one metric per device, used by the
# per-device heartbeat alarms). "0": fleet totals only; the device id stays in the log line.
PER_DEVICE_METRICS = os.environ.get("PER_DEVICE_METRICS", "1") == "1"

_dynamo = None
_firehose = None
_sqs = None


def _clients() -> tuple[Any, Any, Any]:
    global _dynamo, _firehose, _sqs
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
        _firehose = boto3.client("firehose")
        _sqs = boto3.client("sqs")
    return _dynamo, _firehose, _sqs


def reset_clients() -> None:
    """For tests: forget cached clients so moto's fakes are picked up."""
    global _dynamo, _firehose, _sqs
    _dynamo = _firehose = _sqs = None


def _dimensions(device_id: str) -> dict[str, str]:
    return {"DeviceId": device_id} if PER_DEVICE_METRICS else {}


def _reject(device_id: str, reason: str, raw: dict[str, Any], sqs: Any) -> dict[str, Any]:
    metrics.emit(
        NAMESPACE, {"Rejected": 1}, _dimensions(device_id), DeviceId=device_id, reason=reason
    )
    if REJECT_QUEUE_URL:
        sqs.send_message(
            QueueUrl=REJECT_QUEUE_URL,
            MessageBody=json.dumps({"device_id": device_id, "reason": reason, "payload": raw}),
        )
    return {"status": "rejected", "device_id": device_id, "reason": reason}


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    dynamo, firehose, sqs = _clients()
    raw = dict(event)
    device_id = str(raw.pop("device_id", "") or "unknown")
    received_at = int(raw.pop("received_at_ms", time.time() * 1000)) // 1000

    try:
        record = validate(raw, now=received_at)
    except ValidationError as exc:
        return _reject(device_id, str(exc), raw, sqs)

    full = enrich(record, device_id=device_id, received_at=received_at, ttl_days=TTL_DAYS)
    item = to_item(full)

    dynamo.Table(HISTORY_TABLE).put_item(Item=item)
    dynamo.Table(STATUS_TABLE).put_item(Item=item)
    if FIREHOSE_STREAM:
        firehose.put_record(
            DeliveryStreamName=FIREHOSE_STREAM,
            Record={"Data": (json.dumps(full) + "\n").encode()},
        )

    metrics.emit(
        NAMESPACE,
        {"Accepted": 1, "LatencySeconds": full["latency_s"]},
        _dimensions(device_id),
        DeviceId=device_id,
        temp=full["temp"],
        heater=int(full["heater"]),
    )
    return {"status": "accepted", "device_id": device_id, "ts": full["ts"]}
