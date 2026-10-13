"""Shared fixtures: fake AWS credentials and moto-backed tables/streams/queues."""

from __future__ import annotations

import os
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

REGION = "eu-central-1"


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    os.environ.pop("AWS_PROFILE", None)


@pytest.fixture
def aws() -> Iterator[None]:
    with mock_aws():
        yield


@pytest.fixture
def tables(aws: None) -> dict[str, str]:
    ddb = boto3.client("dynamodb", region_name=REGION)
    ddb.create_table(
        TableName="history",
        KeySchema=[
            {"AttributeName": "device_id", "KeyType": "HASH"},
            {"AttributeName": "ts", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "device_id", "AttributeType": "S"},
            {"AttributeName": "ts", "AttributeType": "N"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="status",
        KeySchema=[{"AttributeName": "device_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "device_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return {"history": "history", "status": "status"}


@pytest.fixture
def firehose_to_s3(aws: None) -> dict[str, str]:
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="cold", CreateBucketConfiguration={"LocationConstraint": REGION})
    fh = boto3.client("firehose", region_name=REGION)
    fh.create_delivery_stream(
        DeliveryStreamName="telemetry",
        DeliveryStreamType="DirectPut",
        ExtendedS3DestinationConfiguration={
            "RoleARN": "arn:aws:iam::123456789012:role/firehose",
            "BucketARN": "arn:aws:s3:::cold",
            "Prefix": "telemetry/",
        },
    )
    return {"stream": "telemetry", "bucket": "cold"}


@pytest.fixture
def reject_queue(aws: None) -> str:
    sqs = boto3.client("sqs", region_name=REGION)
    return sqs.create_queue(QueueName="rejects")["QueueUrl"]
