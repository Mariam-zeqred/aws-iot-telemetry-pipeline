"""The one stack: devices -> rule -> Lambda -> hot + cold stores; API + dashboard; alarms."""

from __future__ import annotations

from aws_cdk import CfnOutput, Stack, Tags
from constructs import Construct

from pipeline.api import StatusApi
from pipeline.config import PipelineConfig
from pipeline.dashboard import Dashboard
from pipeline.devices import Devices
from pipeline.ingest import Ingest
from pipeline.monitoring import Monitoring
from pipeline.storage import ColdStorage, HotStorage


class TelemetryPipelineStack(Stack):
    def __init__(
        self, scope: Construct, id: str, cfg: PipelineConfig | None = None, **kwargs: object
    ) -> None:
        super().__init__(scope, id, **kwargs)
        self.cfg = cfg or PipelineConfig.from_context(self)
        Tags.of(self).add("project", self.cfg.project)

        self.devices = Devices(self, "Devices", self.cfg)
        self.hot = HotStorage(self, "Hot", self.cfg)
        self.cold = ColdStorage(self, "Cold", self.cfg)
        self.ingest = Ingest(self, "Ingest", self.cfg, self.hot, self.cold)
        self.api = StatusApi(self, "Api", self.cfg, self.hot)
        self.dashboard = Dashboard(self, "Dashboard", self.cfg, self.api.url)
        self.monitoring = Monitoring(self, "Monitoring", self.cfg, self.ingest.function)

        CfnOutput(self, "ApiUrl", value=self.api.url)
        CfnOutput(self, "DashboardUrl", value=f"https://{self.dashboard.distribution.domain_name}")
        CfnOutput(self, "HistoryTable", value=self.hot.history.table_name)
        CfnOutput(self, "StatusTable", value=self.hot.status.table_name)
        CfnOutput(self, "ColdBucket", value=self.cold.bucket.bucket_name)
        CfnOutput(self, "AthenaWorkGroup", value=self.cold.workgroup.ref)
        CfnOutput(self, "RejectQueueUrl", value=self.ingest.reject_queue.queue_url)
        CfnOutput(self, "AlarmTopicArn", value=self.monitoring.topic.topic_arn)
        CfnOutput(self, "DevicePolicyName", value=self.devices.policy.ref)
