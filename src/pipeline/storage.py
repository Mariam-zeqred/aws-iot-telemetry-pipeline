"""Hot store (DynamoDB, 30-day TTL) and cold store (Firehose -> S3 Parquet -> Glue/Athena)."""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_athena as athena
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kinesisfirehose as firehose
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

from pipeline.config import PipelineConfig

# Columns of the Parquet files, in the order Firehose writes them. Names are lower-case
# because the OpenX JSON SerDe lower-cases keys ("tempOut" -> "tempout").
PARQUET_COLUMNS: list[tuple[str, str]] = [
    ("device_id", "string"),
    ("ts", "bigint"),
    ("received_at", "bigint"),
    ("latency_s", "bigint"),
    ("temp", "double"),
    ("hum", "double"),
    ("tempout", "double"),
    ("humout", "double"),
    ("targettemp", "double"),
    ("heater", "boolean"),
    ("door", "string"),
    ("cycleday", "int"),
    ("cyclecode", "string"),
    ("elapsedsec", "bigint"),
    ("iso", "string"),
    ("delta_in_out", "double"),
    ("expires_at", "bigint"),
    ("day", "string"),
    ("schema_version", "int"),
]
PARTITION_KEYS = ["year", "month", "day_of_month"]
DATA_PREFIX = "telemetry/"


class HotStorage(Construct):
    def __init__(self, scope: Construct, id: str, cfg: PipelineConfig) -> None:
        super().__init__(scope, id)
        removal = RemovalPolicy.RETAIN if cfg.retain_data else RemovalPolicy.DESTROY

        self.history = ddb.Table(
            self,
            "History",
            partition_key=ddb.Attribute(name="device_id", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="ts", type=ddb.AttributeType.NUMBER),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=removal,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=cfg.retain_data
            ),
        )
        self.status = ddb.Table(
            self,
            "Status",
            partition_key=ddb.Attribute(name="device_id", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal,
        )


class ColdStorage(Construct):
    def __init__(self, scope: Construct, id: str, cfg: PipelineConfig) -> None:
        super().__init__(scope, id)
        stack = Stack.of(self)
        removal = RemovalPolicy.RETAIN if cfg.retain_data else RemovalPolicy.DESTROY

        self.bucket = s3.Bucket(
            self,
            "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=removal,
            auto_delete_objects=not cfg.retain_data,
            lifecycle_rules=[
                s3.LifecycleRule(
                    prefix=DATA_PREFIX,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=Duration.days(90),
                        )
                    ],
                ),
                s3.LifecycleRule(prefix="athena-results/", expiration=Duration.days(7)),
            ],
        )

        self.database = glue.CfnDatabase(
            self,
            "Database",
            catalog_id=stack.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(name=f"{cfg.project}_telemetry"),
        )
        location = f"s3://{self.bucket.bucket_name}/{DATA_PREFIX}"
        self.table = glue.CfnTable(
            self,
            "Table",
            catalog_id=stack.account,
            database_name=self.database.ref,
            table_input=glue.CfnTable.TableInputProperty(
                name="readings",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "parquet",
                    "projection.enabled": "true",
                    "projection.year.type": "integer",
                    "projection.year.range": "2026,2040",
                    "projection.month.type": "integer",
                    "projection.month.range": "1,12",
                    "projection.month.digits": "2",
                    "projection.day_of_month.type": "integer",
                    "projection.day_of_month.range": "1,31",
                    "projection.day_of_month.digits": "2",
                    "storage.location.template": location
                    + "year=${year}/month=${month}/day_of_month=${day_of_month}/",
                },
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name=k, type="string") for k in PARTITION_KEYS
                ],
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=location,
                    input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library=(
                            "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                        )
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in PARQUET_COLUMNS
                    ],
                ),
            ),
        )

        role = iam.Role(
            self, "FirehoseRole", assumed_by=iam.ServicePrincipal("firehose.amazonaws.com")
        )
        self.bucket.grant_read_write(role)  # read is needed for multipart uploads' ListBucket
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"],
                resources=[
                    stack.format_arn(service="glue", resource="catalog"),
                    stack.format_arn(
                        service="glue", resource="database", resource_name=self.database.ref
                    ),
                    stack.format_arn(
                        service="glue",
                        resource="table",
                        resource_name=f"{self.database.ref}/{self.table.ref}",
                    ),
                ],
            )
        )
        log_group = logs.LogGroup(
            self, "FirehoseLogs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=removal
        )
        log_group.grant_write(role)

        self.stream = firehose.CfnDeliveryStream(
            self,
            "Stream",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=(
                firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                    bucket_arn=self.bucket.bucket_arn,
                    role_arn=role.role_arn,
                    prefix=(
                        DATA_PREFIX + "year=!{timestamp:yyyy}/month=!{timestamp:MM}/"
                        "day_of_month=!{timestamp:dd}/"
                    ),
                    error_output_prefix=(
                        "errors/!{firehose:error-output-type}/!{timestamp:yyyy/MM/dd}/"
                    ),
                    buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                        interval_in_seconds=300, size_in_m_bs=64
                    ),
                    compression_format="UNCOMPRESSED",  # Parquet has its own (Snappy)
                    data_format_conversion_configuration=(
                        firehose.CfnDeliveryStream.DataFormatConversionConfigurationProperty(
                            enabled=True,
                            input_format_configuration=(
                                firehose.CfnDeliveryStream.InputFormatConfigurationProperty(
                                    deserializer=firehose.CfnDeliveryStream.DeserializerProperty(
                                        open_x_json_ser_de=(
                                            firehose.CfnDeliveryStream.OpenXJsonSerDeProperty(
                                                case_insensitive=True
                                            )
                                        )
                                    )
                                )
                            ),
                            output_format_configuration=(
                                firehose.CfnDeliveryStream.OutputFormatConfigurationProperty(
                                    serializer=firehose.CfnDeliveryStream.SerializerProperty(
                                        parquet_ser_de=(
                                            firehose.CfnDeliveryStream.ParquetSerDeProperty(
                                                compression="SNAPPY"
                                            )
                                        )
                                    )
                                )
                            ),
                            schema_configuration=(
                                firehose.CfnDeliveryStream.SchemaConfigurationProperty(
                                    catalog_id=stack.account,
                                    database_name=self.database.ref,
                                    table_name=self.table.ref,
                                    region=stack.region,
                                    role_arn=role.role_arn,
                                    version_id="LATEST",
                                )
                            ),
                        )
                    ),
                    cloud_watch_logging_options=(
                        firehose.CfnDeliveryStream.CloudWatchLoggingOptionsProperty(
                            enabled=True,
                            log_group_name=log_group.log_group_name,
                            log_stream_name="S3Delivery",
                        )
                    ),
                )
            ),
        )
        self.stream.node.add_dependency(role)

        self.workgroup = athena.CfnWorkGroup(
            self,
            "WorkGroup",
            name=f"{cfg.project}-telemetry",
            recursive_delete_option=not cfg.retain_data,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.bucket.bucket_name}/athena-results/"
                ),
                bytes_scanned_cutoff_per_query=1024 * 1024 * 1024,  # 1 GB: a cost fuse
                publish_cloud_watch_metrics_enabled=True,
            ),
        )
