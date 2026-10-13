"""Static dashboard: private S3 bucket behind CloudFront (origin access control)."""

from __future__ import annotations

from aws_cdk import RemovalPolicy
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as deployment
from constructs import Construct

from pipeline.config import PipelineConfig
from pipeline.paths import DASHBOARD_DIR


class Dashboard(Construct):
    def __init__(self, scope: Construct, id: str, cfg: PipelineConfig, api_url: str) -> None:
        super().__init__(scope, id)
        removal = RemovalPolicy.RETAIN if cfg.retain_data else RemovalPolicy.DESTROY

        self.bucket = s3.Bucket(
            self,
            "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=removal,
            auto_delete_objects=not cfg.retain_data,
        )
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            comment=f"{cfg.project} telemetry dashboard",
        )
        deployment.BucketDeployment(
            self,
            "Deploy",
            sources=[
                deployment.Source.asset(str(DASHBOARD_DIR)),
                # Resolved at deploy time, so index.html can stay a plain static file.
                deployment.Source.json_data("config.json", {"apiUrl": api_url}),
            ],
            destination_bucket=self.bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )
