"""cdk-nag `AwsSolutionsChecks` for the stack, with the findings this design accepts on purpose.

Every suppression carries the reason a reviewer would need. Anything not listed here must be
fixed in the constructs, which is what `tests/test_nag.py` enforces. Run it by hand with
`cdk synth -c nag=true` (prints the findings) or `make nag`.
"""

from __future__ import annotations

from aws_cdk import Aspects, Stack
from cdk_nag import AwsSolutionsChecks, NagPackSuppression, NagSuppressions, RegexAppliesTo

from pipeline.stack import TelemetryPipelineStack

LAMBDA_BASIC_EXECUTION = (
    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)


def _s(rule: str, reason: str, *applies_to: str | RegexAppliesTo) -> NagPackSuppression:
    return NagPackSuppression(id=rule, reason=reason, applies_to=list(applies_to) or None)


def apply(stack: TelemetryPipelineStack, *, verbose: bool = False) -> None:
    """Attach the checks and the suppressions; findings appear as synth annotations."""
    Aspects.of(stack).add(AwsSolutionsChecks(verbose=verbose))
    add_suppressions(stack)


def add_suppressions(stack: TelemetryPipelineStack) -> None:
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            _s(
                "AwsSolutions-IAM4",
                "AWSLambdaBasicExecutionRole only grants CloudWatch Logs CreateLogStream and "
                "PutLogEvents; an inline copy would be the same statement with a longer template.",
                LAMBDA_BASIC_EXECUTION,
            ),
        ],
    )

    for bucket in (stack.cold.bucket, stack.dashboard.bucket):
        NagSuppressions.add_resource_suppressions(
            bucket,
            [
                _s(
                    "AwsSolutions-S1",
                    "Server access logs need a third bucket and add a request per request; the "
                    "buckets are private (BLOCK_ALL, TLS only) and written by Firehose / CDK only. "
                    "CloudTrail data events are the audit path if one is ever required.",
                )
            ],
        )

    NagSuppressions.add_resource_suppressions(
        stack.cold.node.find_child("FirehoseRole"),
        [
            _s(
                "AwsSolutions-IAM5",
                "Firehose names the objects it writes (timestamp prefixes), so GetObject, "
                "PutObject and AbortMultipartUpload must cover every key under the one cold "
                "bucket. Actions are the six the Firehose S3-destination documentation lists; "
                "no Delete.",
                RegexAppliesTo(regex="/^Resource::<ColdBucket[0-9A-F]+\\.Arn>\\/\\*$/g"),
            )
        ],
        apply_to_children=True,
    )

    NagSuppressions.add_resource_suppressions(
        stack.ingest.reject_queue,
        [
            _s(
                "AwsSolutions-SQS3",
                "This queue *is* the dead-letter destination: rejected telemetry parks here for 14 "
                "days to be inspected. A DLQ for the DLQ would not add information.",
            )
        ],
    )

    NagSuppressions.add_resource_suppressions(
        stack.dashboard.distribution,
        [
            _s(
                "AwsSolutions-CFR1",
                "Public read-only dashboard of fleet temperatures; no country restriction wanted.",
            ),
            _s(
                "AwsSolutions-CFR2",
                "A WAF web ACL costs more per month than the whole 10-device pipeline and the "
                "origin is a private bucket with one static page; API Gateway throttling guards "
                "the API.",
            ),
            _s(
                "AwsSolutions-CFR3",
                "Access logs for a one-page dashboard are not worth a logging bucket (see S1).",
            ),
            _s(
                "AwsSolutions-CFR4",
                "No custom domain: with the default *.cloudfront.net certificate CloudFront fixes "
                "MinimumProtocolVersion to TLSv1 and rejects a higher value. Add an ACM "
                "certificate and TLS_V1_2_2021 when a domain exists.",
            ),
        ],
    )

    NagSuppressions.add_resource_suppressions(
        stack.api.api,
        [
            _s(
                "AwsSolutions-APIG4",
                "Read-only GET API, throttled to 10 req/s, exposing fleet temperatures; README "
                "'Security notes' says to add a Cognito or IAM authoriser before real hives are "
                "on it.",
            )
        ],
        apply_to_children=True,
    )

    for table in (stack.hot.history, stack.hot.status):
        NagSuppressions.add_resource_suppressions(
            table,
            [
                _s(
                    "AwsSolutions-DDB3",
                    "The tables hold a 30-day TTL copy of data whose system of record is the S3 "
                    "Parquet archive; `retain_data=true` turns point-in-time recovery on.",
                )
            ],
        )

    # The aws-s3-deployment singleton Lambda: runtime and S3 grants are chosen by the CDK
    # construct, not by this repo.
    for child in Stack.of(stack).node.children:
        if child.node.id.startswith("Custom::CDKBucketDeployment"):
            NagSuppressions.add_resource_suppressions(
                child,
                [
                    _s(
                        "AwsSolutions-L1",
                        "Runtime pinned by aws-s3-deployment's BucketDeployment provider.",
                    ),
                    _s(
                        "AwsSolutions-IAM5",
                        "BucketDeployment copies from the CDK assets bucket into the dashboard "
                        "bucket; both wildcards are the construct's own grants.",
                    ),
                ],
                apply_to_children=True,
            )
