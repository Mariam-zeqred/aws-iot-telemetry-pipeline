"""cdk-nag: the AwsSolutions pack must pass with only the documented suppressions."""

from __future__ import annotations

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template

from pipeline import nag
from pipeline.config import PipelineConfig
from pipeline.stack import TelemetryPipelineStack

CFG = PipelineConfig(
    device_ids=("hive-1C5A00", "hive-SIM001"),
    certificate_arns={"hive-SIM001": "arn:aws:iot:eu-central-1:123456789012:cert/abc"},
    alarm_email="alerts@example.com",
)


@pytest.fixture(scope="module")
def checked() -> tuple[TelemetryPipelineStack, Annotations]:
    app = cdk.App()
    stack = TelemetryPipelineStack(app, "Nag", cfg=CFG)
    nag.apply(stack)
    app.synth()
    return stack, Annotations.from_stack(stack)


def _messages(found: list) -> list[str]:
    return [f"{m.id}: {m.entry.data.splitlines()[0]}" for m in found]


def test_no_unsuppressed_errors(checked: tuple) -> None:
    _, ann = checked
    errors = ann.find_error("*", Match.string_like_regexp("AwsSolutions-.*"))
    assert not errors, "\n".join(_messages(errors))


def test_no_unsuppressed_warnings(checked: tuple) -> None:
    _, ann = checked
    warnings = ann.find_warning("*", Match.string_like_regexp("AwsSolutions-.*"))
    assert not warnings, "\n".join(_messages(warnings))


def test_suppressions_are_recorded_with_reasons(checked: tuple) -> None:
    stack, _ = checked
    template = Template.from_stack(stack).to_json()
    reasons = [
        s["reason"]
        for res in template["Resources"].values()
        for s in res.get("Metadata", {}).get("cdk_nag", {}).get("rules_to_suppress", [])
    ]
    assert reasons and all(len(r) > 40 for r in reasons)


def test_nag_fixes_are_in_the_template(checked: tuple) -> None:
    stack, _ = checked
    t = Template.from_stack(stack)
    t.has_resource_properties(
        "AWS::KinesisFirehose::DeliveryStream",
        {"DeliveryStreamEncryptionConfigurationInput": {"KeyType": "AWS_OWNED_CMK"}},
    )
    t.has_resource_properties(
        "AWS::ApiGatewayV2::Stage", {"AccessLogSettings": {"Format": Match.any_value()}}
    )
    topic_policies = t.find_resources("AWS::SNS::TopicPolicy")
    assert any("aws:SecureTransport" in json.dumps(p) for p in topic_policies.values())
    for policy in t.find_resources("AWS::IAM::Policy").values():
        doc = json.dumps(policy["Properties"]["PolicyDocument"])
        if "s3:PutObject" in doc and "firehose" in json.dumps(policy).lower():
            assert "s3:DeleteObject" not in doc
    ours = {"ingest.handler.handler", "status_api.handler.handler", "heartbeat.handler.handler"}
    runtimes = {
        fn["Properties"]["Handler"]: fn["Properties"]["Runtime"]
        for fn in t.find_resources("AWS::Lambda::Function").values()
        if fn["Properties"].get("Handler") in ours
    }
    assert set(runtimes) == ours and set(runtimes.values()) == {"python3.14"}
