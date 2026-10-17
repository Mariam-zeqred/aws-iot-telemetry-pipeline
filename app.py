#!/usr/bin/env python3
"""CDK entry point. `cdk synth` only; this repo is never deployed from CI (see README)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import aws_cdk as cdk  # noqa: E402

from pipeline.stack import TelemetryPipelineStack  # noqa: E402

app = cdk.App()
stack = TelemetryPipelineStack(
    app,
    "IncubatorTelemetryPipeline",
    description="IoT Core -> Lambda -> DynamoDB + Firehose/S3/Athena, status API and dashboard",
)
if app.node.try_get_context("nag"):  # cdk synth -c nag=true: print cdk-nag findings
    from pipeline.nag import apply  # noqa: E402 (dev dependency, imported only when asked)

    apply(stack, verbose=True)
app.synth()
