"""Read-only status API: HTTP API (API Gateway v2) -> status Lambda -> DynamoDB."""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from pipeline.config import PipelineConfig
from pipeline.ingest import LAMBDA_EXCLUDE
from pipeline.paths import LAMBDAS_DIR
from pipeline.storage import HotStorage


class StatusApi(Construct):
    def __init__(self, scope: Construct, id: str, cfg: PipelineConfig, hot: HotStorage) -> None:
        super().__init__(scope, id)
        removal = RemovalPolicy.RETAIN if cfg.retain_data else RemovalPolicy.DESTROY

        log_group = logs.LogGroup(
            self, "Logs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal
        )
        self.function = lambda_.Function(
            self,
            "Function",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            code=lambda_.Code.from_asset(str(LAMBDAS_DIR), exclude=LAMBDA_EXCLUDE),
            handler="status_api.handler.handler",
            memory_size=256,
            timeout=Duration.seconds(10),
            log_group=log_group,
            environment={
                "HISTORY_TABLE": hot.history.table_name,
                "STATUS_TABLE": hot.status.table_name,
                "STALE_AFTER_S": str(cfg.stale_after_s),
            },
        )
        hot.history.grant_read_data(self.function)
        hot.status.grant_read_data(self.function)

        self.api = apigw.HttpApi(
            self,
            "HttpApi",
            api_name=f"{cfg.project}-status",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigw.CorsHttpMethod.GET],
                allow_headers=["content-type"],
                max_age=Duration.hours(1),
            ),
        )
        integration = integrations.HttpLambdaIntegration("StatusIntegration", self.function)
        for path in ("/health", "/devices", "/devices/{device_id}", "/devices/{device_id}/history"):
            self.api.add_routes(path=path, methods=[apigw.HttpMethod.GET], integration=integration)

        # Throttle the default stage: a dashboard polling every 10 s needs ~0.1 req/s.
        stage = self.api.default_stage
        assert stage is not None
        cfn_stage = stage.node.default_child
        assert isinstance(cfn_stage, apigw.CfnStage)
        cfn_stage.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=20, throttling_rate_limit=10
        )

    @property
    def url(self) -> str:
        assert self.api.url is not None
        return self.api.url
