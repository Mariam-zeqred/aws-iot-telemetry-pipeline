import json

import boto3
import pytest

from ingest import handler as ingest
from tests.conftest import REGION
from tests.test_telemetry import GOOD, NOW


@pytest.fixture
def env(
    monkeypatch: pytest.MonkeyPatch, tables: dict, firehose_to_s3: dict, reject_queue: str
) -> str:
    monkeypatch.setattr(ingest, "HISTORY_TABLE", tables["history"])
    monkeypatch.setattr(ingest, "STATUS_TABLE", tables["status"])
    monkeypatch.setattr(ingest, "FIREHOSE_STREAM", firehose_to_s3["stream"])
    monkeypatch.setattr(ingest, "REJECT_QUEUE_URL", reject_queue)
    monkeypatch.setattr(ingest, "TTL_DAYS", 30)
    ingest.reset_clients()
    return reject_queue


def _event(**over: object) -> dict:
    return dict(GOOD, device_id="hive-1C5A00", received_at_ms=NOW * 1000, **over)


def test_accepted_message_lands_everywhere(env: str, capsys: pytest.CaptureFixture) -> None:
    out = ingest.handler(_event())
    assert out["status"] == "accepted"

    ddb = boto3.resource("dynamodb", region_name=REGION)
    hist = ddb.Table("history").get_item(Key={"device_id": "hive-1C5A00", "ts": GOOD["ts"]})["Item"]
    assert float(hist["temp"]) == 26.87
    assert int(hist["expires_at"]) == GOOD["ts"] + 30 * 86400
    status = ddb.Table("status").get_item(Key={"device_id": "hive-1C5A00"})["Item"]
    assert int(status["ts"]) == GOOD["ts"]

    s3 = boto3.client("s3", region_name=REGION)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="cold").get("Contents", [])]
    assert len(keys) == 1 and keys[0].startswith("telemetry/")
    body = s3.get_object(Bucket="cold", Key=keys[0])["Body"].read().decode()
    assert json.loads(body.strip())["device_id"] == "hive-1C5A00"

    emf = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert emf["Accepted"] == 1 and emf["DeviceId"] == "hive-1C5A00"
    assert emf["_aws"]["CloudWatchMetrics"][0]["Namespace"] == ingest.NAMESPACE


def test_status_is_latest_wins(env: str) -> None:
    ingest.handler(_event(ts=GOOD["ts"], temp=26.0))
    ingest.handler(_event(ts=GOOD["ts"] + 15, temp=26.5))
    ddb = boto3.resource("dynamodb", region_name=REGION)
    assert (
        float(ddb.Table("status").get_item(Key={"device_id": "hive-1C5A00"})["Item"]["temp"])
        == 26.5
    )
    assert ddb.Table("history").scan()["Count"] == 2


def test_rejected_message_goes_to_queue_not_tables(env: str, capsys: pytest.CaptureFixture) -> None:
    out = ingest.handler(_event(hum=140))
    assert out == {"status": "rejected", "device_id": "hive-1C5A00", "reason": "range:hum"}

    sqs = boto3.client("sqs", region_name=REGION)
    msgs = sqs.receive_message(QueueUrl=env, MaxNumberOfMessages=10).get("Messages", [])
    assert len(msgs) == 1
    body = json.loads(msgs[0]["Body"])
    assert body["reason"] == "range:hum" and body["payload"]["hum"] == 140

    ddb = boto3.resource("dynamodb", region_name=REGION)
    assert ddb.Table("history").scan()["Count"] == 0
    emf = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert emf["Rejected"] == 1 and emf["reason"] == "range:hum"


def test_received_at_defaults_to_now_when_rule_omits_it(env: str) -> None:
    import time

    ev = _event(ts=int(time.time()))
    ev.pop("received_at_ms")
    assert ingest.handler(ev)["status"] == "accepted"
