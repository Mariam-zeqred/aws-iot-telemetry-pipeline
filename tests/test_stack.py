"""Synthesise the stack in-process and assert the properties that matter for cost and safety."""

from __future__ import annotations

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from pipeline.config import PipelineConfig
from pipeline.stack import TelemetryPipelineStack

CFG = PipelineConfig(
    device_ids=("hive-1C5A00", "hive-SIM001"),
    certificate_arns={"hive-SIM001": "arn:aws:iot:eu-central-1:123456789012:cert/abc"},
)


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = TelemetryPipelineStack(app, "Test", cfg=CFG)
    return Template.from_stack(stack)


def test_resource_counts(template: Template) -> None:
    template.resource_count_is("AWS::IoT::Thing", 2)
    template.resource_count_is("AWS::IoT::Policy", 1)
    template.resource_count_is("AWS::IoT::TopicRule", 1)
    template.resource_count_is("AWS::DynamoDB::Table", 2)
    template.resource_count_is("AWS::KinesisFirehose::DeliveryStream", 1)
    template.resource_count_is("AWS::Glue::Table", 1)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Route", 4)
    # one heartbeat alarm per device + fleet silent + rejects + lambda errors
    template.resource_count_is("AWS::CloudWatch::Alarm", 2 + 3)
    # only the device with a certificate ARN gets attachments
    template.resource_count_is("AWS::IoT::ThingPrincipalAttachment", 1)
    template.resource_count_is("AWS::IoT::PolicyPrincipalAttachment", 1)


def test_history_table_has_ttl_and_on_demand_billing(template: Template) -> None:
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
            "KeySchema": [
                {"AttributeName": "device_id", "KeyType": "HASH"},
                {"AttributeName": "ts", "KeyType": "RANGE"},
            ],
        },
    )


def test_device_policy_is_scoped_to_the_connecting_thing(template: Template) -> None:
    policies = template.find_resources("AWS::IoT::Policy")
    doc = json.dumps(next(iter(policies.values()))["Properties"]["PolicyDocument"])
    assert "${iot:Connection.Thing.ThingName}" in doc
    assert '"Resource": "*"' not in doc and "'Resource': '*'" not in doc
    assert "iot:Connection.Thing.IsAttached" in doc
    assert "topic/incubator/${iot:Connection.Thing.ThingName}/telemetry" in doc


def test_rule_sql_and_error_action(template: Template) -> None:
    template.has_resource_properties(
        "AWS::IoT::TopicRule",
        {
            "TopicRulePayload": {
                "Sql": Match.string_like_regexp(
                    r"SELECT \*, topic\(2\) AS device_id, timestamp\(\) AS received_at_ms "
                    r"FROM 'incubator/\+/telemetry'"
                ),
                "Actions": [{"Lambda": Match.any_value()}],
                "ErrorAction": {"CloudwatchLogs": Match.any_value()},
            }
        },
    )


def test_firehose_writes_parquet_with_glue_schema(template: Template) -> None:
    template.has_resource_properties(
        "AWS::KinesisFirehose::DeliveryStream",
        {
            "ExtendedS3DestinationConfiguration": {
                "DataFormatConversionConfiguration": {
                    "Enabled": True,
                    "OutputFormatConfiguration": {
                        "Serializer": {"ParquetSerDe": {"Compression": "SNAPPY"}}
                    },
                },
                "BufferingHints": {"SizeInMBs": 64},
                "Prefix": Match.string_like_regexp(r"^telemetry/year=!\{timestamp:yyyy\}"),
            }
        },
    )


def test_buckets_are_private_and_tls_only(template: Template) -> None:
    for bucket in template.find_resources("AWS::S3::Bucket").values():
        cfg = bucket["Properties"]["PublicAccessBlockConfiguration"]
        assert all(cfg[k] for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls"))
    policies = template.find_resources("AWS::S3::BucketPolicy")
    assert all(
        "aws:SecureTransport" in json.dumps(p["Properties"]["PolicyDocument"])
        for p in policies.values()
    )


def test_ingest_lambda_wiring(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "ingest.handler.handler",
            "Runtime": "python3.12",
            "Environment": {
                "Variables": {
                    "TTL_DAYS": "30",
                    "HISTORY_TABLE": Match.any_value(),
                    "FIREHOSE_STREAM": Match.any_value(),
                    "REJECT_QUEUE_URL": Match.any_value(),
                }
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Permission",
        {"Principal": "iot.amazonaws.com", "Action": "lambda:InvokeFunction"},
    )


def test_heartbeat_alarm_treats_missing_data_as_breaching(template: Template) -> None:
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "AlarmName": "incubator-hive-1C5A00-missing-heartbeat",
            "MetricName": "Accepted",
            "Namespace": "Incubator/Telemetry",
            "Dimensions": [{"Name": "DeviceId", "Value": "hive-1C5A00"}],
            "TreatMissingData": "breaching",
            "ComparisonOperator": "LessThanThreshold",
            "Threshold": 1,
            "EvaluationPeriods": 2,
        },
    )


def test_api_is_throttled_and_cors_is_get_only(template: Template) -> None:
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {"DefaultRouteSettings": {"ThrottlingBurstLimit": 20, "ThrottlingRateLimit": 10}},
    )
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {"CorsConfiguration": {"AllowMethods": ["GET"], "AllowOrigins": ["*"]}},
    )


def test_cloudfront_uses_oac_and_https(template: Template) -> None:
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "DefaultCacheBehavior": {"ViewerProtocolPolicy": "redirect-to-https"},
                "DefaultRootObject": "index.html",
            }
        },
    )


def test_retain_data_switches_removal_policies() -> None:
    app = cdk.App()
    stack = TelemetryPipelineStack(app, "Keep", cfg=PipelineConfig(retain_data=True))
    t = Template.from_stack(stack)
    tables = t.find_resources("AWS::DynamoDB::Table")
    assert all(r["DeletionPolicy"] == "Retain" for r in tables.values())
    t.resource_count_is("Custom::S3AutoDeleteObjects", 0)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        PipelineConfig(ttl_days=0).validate()
    with pytest.raises(ValueError):
        PipelineConfig(device_ids=("bad id",)).validate()
    with pytest.raises(ValueError):
        PipelineConfig(certificate_arns={"ghost": "arn"}).validate()


def test_config_from_context_coerces_strings() -> None:
    app = cdk.App(
        context={
            "device_ids": "a,b",
            "ttl_days": "7",
            "retain_data": "true",
            "certificate_arns": '{"a": "arn:x"}',
        }
    )
    cfg = PipelineConfig.from_context(app)
    assert cfg.device_ids == ("a", "b") and cfg.ttl_days == 7 and cfg.retain_data is True
    assert cfg.certificate_arns == {"a": "arn:x"}
