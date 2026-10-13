#!/usr/bin/env python3
"""CDK entry point. `cdk synth` only; this repo is never deployed from CI (see README)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import aws_cdk as cdk  # noqa: E402

from pipeline.stack import TelemetryPipelineStack  # noqa: E402

app = cdk.App()
TelemetryPipelineStack(
    app,
    "IncubatorTelemetryPipeline",
    description="IoT Core -> Lambda -> DynamoDB + Firehose/S3/Athena, status API and dashboard",
)
app.synth()
