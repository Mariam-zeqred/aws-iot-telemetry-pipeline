"""Alarms: stale devices (scheduled scan), optional per-device heartbeats, rejects, errors.

Two ways to notice a silent device live side by side:

* `per_device_metrics = true` (default, small fleets): the ingest Lambda publishes `Accepted`
  with a DeviceId dimension and one alarm per device in `cdk.json` watches it. Simple, and
  the alarm names the hive. It costs one metric and one alarm per device.
* The heartbeat Lambda (always on): every `heartbeat_period_minutes` it scans the status table,
  counts devices whose last message is older than `stale_after_s` and publishes one
  `StaleDevices` metric. One alarm on it covers any fleet size, and because missing data is
  treated as breaching, a heartbeat Lambda that stops running raises the same alarm.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from pipeline.config import PipelineConfig
from pipeline.ingest import LAMBDA_EXCLUDE
from pipeline.paths import LAMBDAS_DIR
from pipeline.storage import HotStorage


class Monitoring(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        cfg: PipelineConfig,
        ingest_fn: lambda_.Function,
        hot: HotStorage,
    ) -> None:
        super().__init__(scope, id)
        removal = RemovalPolicy.RETAIN if cfg.retain_data else RemovalPolicy.DESTROY
        self.topic = sns.Topic(
            self, "Alarms", display_name=f"{cfg.project}-telemetry-alarms", enforce_ssl=True
        )
        if cfg.alarm_email:
            self.topic.add_subscription(subs.EmailSubscription(cfg.alarm_email))
        action = cw_actions.SnsAction(self.topic)
        period = Duration.minutes(cfg.heartbeat_period_minutes)

        def accepted(dimensions: dict[str, str] | None) -> cw.Metric:
            return cw.Metric(
                namespace=cfg.metric_namespace,
                metric_name="Accepted",
                dimensions_map=dimensions,
                statistic="Sum",
                period=period,
            )

        # --- scheduled scan of the status table -> StaleDevices metric -> one alarm
        heartbeat_logs = logs.LogGroup(
            self, "HeartbeatLogs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal
        )
        self.heartbeat = lambda_.Function(
            self,
            "Heartbeat",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            code=lambda_.Code.from_asset(str(LAMBDAS_DIR), exclude=LAMBDA_EXCLUDE),
            handler="heartbeat.handler.handler",
            memory_size=256,
            timeout=Duration.seconds(60),
            log_group=heartbeat_logs,
            environment={
                "STATUS_TABLE": hot.status.table_name,
                "STALE_AFTER_S": str(cfg.stale_after_s),
                "METRIC_NAMESPACE": cfg.metric_namespace,
            },
        )
        hot.status.grant_read_data(self.heartbeat)
        self.schedule = events.Rule(
            self,
            "HeartbeatSchedule",
            description="Scan the status table for stale devices",
            schedule=events.Schedule.rate(period),
            targets=[targets.LambdaFunction(self.heartbeat, retry_attempts=0)],
        )
        # Period = two schedule ticks so EventBridge jitter cannot leave a window empty; then a
        # window with no datapoint means the heartbeat Lambda itself is not running.
        self.stale_devices = cw.Alarm(
            self,
            "StaleDevices",
            alarm_name=f"{cfg.project}-stale-devices",
            alarm_description=(
                f"One or more devices silent for over {cfg.stale_after_s // 60} min "
                "(ids in the heartbeat Lambda's log), or the heartbeat Lambda is not running"
            ),
            metric=cw.Metric(
                namespace=cfg.metric_namespace,
                metric_name="StaleDevices",
                statistic="Maximum",
                period=Duration.minutes(2 * cfg.heartbeat_period_minutes),
            ),
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.BREACHING,
        )
        self.stale_devices.add_alarm_action(action)
        self.stale_devices.add_ok_action(action)

        # --- per-device heartbeats (small fleets only)
        self.heartbeat_alarms: dict[str, cw.Alarm] = {}
        if cfg.per_device_metrics:
            for device_id in cfg.device_ids:
                alarm = cw.Alarm(
                    self,
                    f"Heartbeat-{device_id}",
                    alarm_name=f"{cfg.project}-{device_id}-missing-heartbeat",
                    alarm_description=(
                        f"No accepted telemetry from {device_id} for "
                        f"{cfg.heartbeat_missing_periods} x {cfg.heartbeat_period_minutes} min"
                    ),
                    metric=accepted({"DeviceId": device_id}),
                    threshold=1,
                    comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
                    evaluation_periods=cfg.heartbeat_missing_periods,
                    treat_missing_data=cw.TreatMissingData.BREACHING,
                )
                alarm.add_alarm_action(action)
                alarm.add_ok_action(action)
                self.heartbeat_alarms[device_id] = alarm

        self.fleet_silent = cw.Alarm(
            self,
            "FleetSilent",
            alarm_name=f"{cfg.project}-fleet-silent",
            alarm_description="No device has delivered telemetry; check the broker or the rule",
            metric=accepted(None),
            threshold=1,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            evaluation_periods=cfg.heartbeat_missing_periods,
            treat_missing_data=cw.TreatMissingData.BREACHING,
        )
        self.fleet_silent.add_alarm_action(action)

        self.rejects = cw.Alarm(
            self,
            "Rejects",
            alarm_name=f"{cfg.project}-rejected-messages",
            alarm_description="Messages failing validation: a firmware build is sending bad data",
            metric=cw.Metric(
                namespace=cfg.metric_namespace,
                metric_name="Rejected",
                statistic="Sum",
                period=period,
            ),
            threshold=10,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.rejects.add_alarm_action(action)

        self.lambda_errors = cw.Alarm(
            self,
            "IngestErrors",
            alarm_name=f"{cfg.project}-ingest-errors",
            metric=ingest_fn.metric_errors(period=period, statistic="Sum"),
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self.lambda_errors.add_alarm_action(action)
