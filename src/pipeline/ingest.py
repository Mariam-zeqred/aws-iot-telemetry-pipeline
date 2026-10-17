"""Topic rule -> ingest Lambda -> (DynamoDB, Firehose, reject queue)."""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from pipeline.config import PipelineConfig
from pipeline.paths import LAMBDAS_DIR
from pipeline.storage import ColdStorage, HotStorage

LAMBDA_EXCLUDE = ["**/__pycache__", "*.pyc", "**/.pytest_cache"]


class Ingest(Construct):
    def __init__(
        self, scope: Construct, id: str, cfg: PipelineConfig, hot: HotStorage, cold: ColdStorage
    ) -> None:
        super().__init__(scope, id)
        stack = Stack.of(self)
        removal = RemovalPolicy.RETAIN if cfg.retain_data else RemovalPolicy.DESTROY

        self.reject_queue = sqs.Queue(
            self,
            "Rejects",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )

        log_group = logs.LogGroup(
            self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal
        )
        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            code=lambda_.Code.from_asset(str(LAMBDAS_DIR), exclude=LAMBDA_EXCLUDE),
            handler="ingest.handler.handler",
            memory_size=256,
            timeout=Duration.seconds(10),
            log_group=log_group,
            environment={
                "HISTORY_TABLE": hot.history.table_name,
                "STATUS_TABLE": hot.status.table_name,
                "FIREHOSE_STREAM": cold.stream.ref,
                "REJECT_QUEUE_URL": self.reject_queue.queue_url,
                "TTL_DAYS": str(cfg.ttl_days),
                "METRIC_NAMESPACE": cfg.metric_namespace,
                "PER_DEVICE_METRICS": "1" if cfg.per_device_metrics else "0",
            },
        )
        hot.history.grant_write_data(self.function)
        hot.status.grant_write_data(self.function)
        self.reject_queue.grant_send_messages(self.function)
        self.function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["firehose:PutRecord", "firehose:PutRecordBatch"],
                resources=[cold.stream.attr_arn],
            )
        )

        rule_error_logs = logs.LogGroup(
            self, "RuleErrors", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal
        )
        rule_role = iam.Role(self, "RuleRole", assumed_by=iam.ServicePrincipal("iot.amazonaws.com"))
        rule_error_logs.grant_write(rule_role)

        self.rule = iot.CfnTopicRule(
            self,
            "Rule",
            rule_name=f"{cfg.project}_telemetry_to_lambda",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                aws_iot_sql_version="2016-03-23",
                sql=(
                    "SELECT *, topic(2) AS device_id, timestamp() AS received_at_ms "
                    f"FROM '{cfg.topic_prefix}/+/telemetry'"
                ),
                rule_disabled=False,
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=self.function.function_arn
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=rule_error_logs.log_group_name, role_arn=rule_role.role_arn
                    )
                ),
            ),
        )
        self.function.add_permission(
            "IotInvoke",
            principal=iam.ServicePrincipal("iot.amazonaws.com"),
            source_arn=self.rule.attr_arn,
            source_account=stack.account,
        )
