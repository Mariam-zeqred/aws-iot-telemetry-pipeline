"""Alarms: missing heartbeat per device and for the fleet, rejects, Lambda errors."""

from __future__ import annotations

from aws_cdk import Duration
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from pipeline.config import PipelineConfig


class Monitoring(Construct):
    def __init__(
        self, scope: Construct, id: str, cfg: PipelineConfig, ingest_fn: lambda_.Function
    ) -> None:
        super().__init__(scope, id)
        self.topic = sns.Topic(self, "Alarms", display_name=f"{cfg.project}-telemetry-alarms")
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

        self.heartbeat_alarms: dict[str, cw.Alarm] = {}
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
