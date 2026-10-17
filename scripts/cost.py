#!/usr/bin/env python3
"""Monthly cost estimate of the pipeline at 10 and 1 000 devices.

Unit prices are for US East (N. Virginia), typed in by hand from the public pricing pages
listed in PRICES. They could not be re-checked from the build sandbox (aws.amazon.com was
unreachable there), so treat the table as an estimate and re-read the pages before quoting
it. Free-tier allowances are ignored except where noted, which makes the small-fleet figure
pessimistic.

    python scripts/cost.py            # markdown table
    python scripts/cost.py --json     # machine-readable

Two monitoring designs are costed (see src/pipeline/monitoring.py): `per_device_metrics`
on (one Accepted/Rejected/Latency metric set and one heartbeat alarm per device) and off
(fleet totals plus the scheduled StaleDevices scan only). The scan itself runs in both.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

# (price, unit, source page)
PRICES: dict[str, tuple[float, str, str]] = {
    "iot_connectivity_per_million_minutes": (
        0.08,
        "USD per million connection-minutes",
        "https://aws.amazon.com/iot-core/pricing/",
    ),
    "iot_messaging_per_million": (
        1.00,
        "USD per million messages (<=5 KB each), first 1 B/month",
        "https://aws.amazon.com/iot-core/pricing/",
    ),
    "iot_rules_triggered_per_million": (
        0.15,
        "USD per million rules triggered",
        "https://aws.amazon.com/iot-core/pricing/",
    ),
    "iot_actions_per_million": (
        0.15,
        "USD per million rule actions",
        "https://aws.amazon.com/iot-core/pricing/",
    ),
    "lambda_requests_per_million": (
        0.20,
        "USD per million requests",
        "https://aws.amazon.com/lambda/pricing/",
    ),
    "lambda_gb_second": (
        0.0000133334,
        "USD per GB-second (arm64)",
        "https://aws.amazon.com/lambda/pricing/",
    ),
    "dynamodb_write_per_million": (
        0.625,
        "USD per million write request units (on-demand)",
        "https://aws.amazon.com/dynamodb/pricing/on-demand/",
    ),
    "dynamodb_read_per_million": (
        0.125,
        "USD per million read request units (on-demand)",
        "https://aws.amazon.com/dynamodb/pricing/on-demand/",
    ),
    "dynamodb_storage_gb_month": (
        0.25,
        "USD per GB-month",
        "https://aws.amazon.com/dynamodb/pricing/on-demand/",
    ),
    "firehose_ingest_per_gb": (
        0.029,
        "USD per GB ingested (records rounded up to 5 KB)",
        "https://aws.amazon.com/firehose/pricing/",
    ),
    "firehose_conversion_per_gb": (
        0.018,
        "USD per GB converted to Parquet",
        "https://aws.amazon.com/firehose/pricing/",
    ),
    "s3_storage_gb_month": (
        0.023,
        "USD per GB-month (Standard)",
        "https://aws.amazon.com/s3/pricing/",
    ),
    "s3_put_per_thousand": (
        0.005,
        "USD per 1 000 PUT requests",
        "https://aws.amazon.com/s3/pricing/",
    ),
    "athena_per_tb_scanned": (5.0, "USD per TB scanned", "https://aws.amazon.com/athena/pricing/"),
    "cloudwatch_metric_month": (
        0.30,
        "USD per custom metric-month (first 10 000)",
        "https://aws.amazon.com/cloudwatch/pricing/",
    ),
    "cloudwatch_alarm_month": (
        0.10,
        "USD per standard alarm-month",
        "https://aws.amazon.com/cloudwatch/pricing/",
    ),
    "cloudwatch_logs_per_gb": (
        0.50,
        "USD per GB of logs ingested",
        "https://aws.amazon.com/cloudwatch/pricing/",
    ),
    "apigw_http_per_million": (
        1.00,
        "USD per million HTTP API requests",
        "https://aws.amazon.com/api-gateway/pricing/",
    ),
    "sqs_per_million": (0.40, "USD per million requests", "https://aws.amazon.com/sqs/pricing/"),
}

MESSAGE_BYTES = 350  # one incubator record as JSON
ENRICHED_BYTES = 450
LAMBDA_GB_S_PER_INVOCATION = 0.256 * 0.12  # 256 MB x ~120 ms
EMF_LOG_BYTES = 400
DASHBOARD_REQUESTS_PER_MONTH = 3 * 3600 * 24 * 30 / 15 * 2  # 3 viewers polling 2 calls / 15 s
ATHENA_TB_SCANNED_PER_MONTH = 0.01  # a few ad-hoc queries over Parquet
HEARTBEAT_PERIOD_MIN = 5  # cdk.json heartbeat_period_minutes
HEARTBEAT_GB_S_PER_RUN = 0.256 * 0.5  # 256 MB x ~0.5 s for a scan of a few thousand items
HEARTBEAT_LOG_BYTES = 600
PER_DEVICE_METRIC_NAMES = 3  # Accepted, LatencySeconds, Rejected (the last only if it ever fires)
FLEET_METRIC_NAMES = 3 + 3  # the same three fleet-wide + StaleDevices, OnlineDevices, KnownDevices
FIXED_ALARMS = 4  # stale devices, fleet silent, rejects, ingest errors


@dataclass
class Line:
    service: str
    quantity: str
    usd: float


def estimate(
    devices: int, interval_s: int, ttl_days: int = 30, per_device_metrics: bool = True
) -> list[Line]:
    p = {k: v[0] for k, v in PRICES.items()}
    msgs = devices * (30 * 86400 / interval_s)
    minutes = devices * 30 * 24 * 60
    scans = 30 * 24 * 60 / HEARTBEAT_PERIOD_MIN
    # one eventually-consistent scan reads every status item: 0.5 RRU per 4 KB
    scan_rru = -(-devices * ENRICHED_BYTES // 4096) * 0.5
    metric_names = FLEET_METRIC_NAMES + (
        PER_DEVICE_METRIC_NAMES * devices if per_device_metrics else 0
    )
    alarms = FIXED_ALARMS + (devices if per_device_metrics else 0)
    mode = "per device" if per_device_metrics else "fleet only"
    log_gb = (msgs * EMF_LOG_BYTES + scans * HEARTBEAT_LOG_BYTES) / 1e9
    lines = [
        Line(
            "IoT Core connectivity",
            f"{minutes / 1e6:.2f} M minutes",
            minutes / 1e6 * p["iot_connectivity_per_million_minutes"],
        ),
        Line(
            "IoT Core messaging",
            f"{msgs / 1e6:.2f} M messages",
            msgs / 1e6 * p["iot_messaging_per_million"],
        ),
        Line(
            "IoT rules + actions",
            f"{msgs / 1e6:.2f} M each",
            msgs / 1e6 * (p["iot_rules_triggered_per_million"] + p["iot_actions_per_million"]),
        ),
        Line(
            "Lambda ingest",
            f"{msgs / 1e6:.2f} M invocations",
            msgs / 1e6 * p["lambda_requests_per_million"]
            + msgs * LAMBDA_GB_S_PER_INVOCATION * p["lambda_gb_second"],
        ),
        Line(
            "DynamoDB writes (history + status)",
            f"{2 * msgs / 1e6:.2f} M WRU",
            2 * msgs / 1e6 * p["dynamodb_write_per_million"],
        ),
        Line(
            "DynamoDB storage (30-day TTL)",
            f"{msgs * ENRICHED_BYTES / 1e9:.2f} GB",
            msgs * ENRICHED_BYTES / 1e9 * p["dynamodb_storage_gb_month"],
        ),
        Line(
            "Firehose ingest + Parquet conversion",
            f"{msgs * 5 / 1e6:.2f} GB billed (5 KB min per record)",
            msgs * 5 * 1024 / 1e9 * (p["firehose_ingest_per_gb"] + p["firehose_conversion_per_gb"]),
        ),
        Line(
            "S3 cold storage (first month)",
            f"{msgs * 60 / 1e9:.3f} GB Parquet",
            msgs * 60 / 1e9 * p["s3_storage_gb_month"],
        ),
        Line(
            "Athena",
            f"{ATHENA_TB_SCANNED_PER_MONTH * 1000:.0f} GB scanned",
            ATHENA_TB_SCANNED_PER_MONTH * p["athena_per_tb_scanned"],
        ),
        Line(
            "Heartbeat scan (Lambda + status-table reads)",
            f"{scans:.0f} scans, {scans * scan_rru / 1e6:.2f} M RRU",
            scans / 1e6 * p["lambda_requests_per_million"]
            + scans * HEARTBEAT_GB_S_PER_RUN * p["lambda_gb_second"]
            + scans * scan_rru / 1e6 * p["dynamodb_read_per_million"],
        ),
        Line(
            "CloudWatch metrics",
            f"{metric_names} metrics ({mode})",
            metric_names * p["cloudwatch_metric_month"],
        ),
        Line(
            "CloudWatch alarms",
            f"{alarms} alarms ({mode})",
            alarms * p["cloudwatch_alarm_month"],
        ),
        Line(
            "CloudWatch logs (EMF lines)",
            f"{log_gb:.2f} GB",
            log_gb * p["cloudwatch_logs_per_gb"],
        ),
        Line(
            "API Gateway + status Lambda (3 dashboard viewers)",
            f"{DASHBOARD_REQUESTS_PER_MONTH / 1e6:.2f} M requests",
            DASHBOARD_REQUESTS_PER_MONTH
            / 1e6
            * (p["apigw_http_per_million"] + p["lambda_requests_per_million"]),
        ),
        Line("CloudFront + SQS + Glue catalog", "within always-free tiers", 0.0),
    ]
    return lines


def firehose_if_batched(devices: int, interval_s: int) -> float:
    """What the Firehose line would cost if records were packed to the 5 KB billing unit.

    Not implemented in the stack (see README "Why the Firehose line is left as is"); this is
    the same prices applied to the real bytes instead of the 5 KB-rounded ones.
    """
    p = {k: v[0] for k, v in PRICES.items()}
    msgs = devices * (30 * 86400 / interval_s)
    return (
        msgs
        * ENRICHED_BYTES
        / 1e9
        * (p["firehose_ingest_per_gb"] + p["firehose_conversion_per_gb"])
    )


def markdown(interval_s: int) -> str:
    columns = [
        ("10 devices, per-device alarms", estimate(10, interval_s)),
        ("1 000 devices, per-device alarms", estimate(1000, interval_s)),
        ("1 000 devices, fleet alarm only", estimate(1000, interval_s, per_device_metrics=False)),
    ]
    out = [
        "| Service | " + " | ".join(name for name, _ in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for lines in zip(*(c for _, c in columns), strict=True):
        out.append(f"| {lines[0].service} | " + " | ".join(f"${x.usd:,.2f}" for x in lines) + " |")
    out.append(
        "| **Total per month** | "
        + " | ".join(f"**${sum(x.usd for x in c):,.2f}**" for _, c in columns)
        + " |"
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=15, help="seconds between messages per device")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.json:
        print(
            json.dumps(
                {
                    "interval_s": args.interval,
                    "prices": {
                        k: {"price": v[0], "unit": v[1], "source": v[2]} for k, v in PRICES.items()
                    },
                    "10": [vars(line) for line in estimate(10, args.interval)],
                    "1000": [vars(line) for line in estimate(1000, args.interval)],
                    "1000_fleet_only": [
                        vars(line)
                        for line in estimate(1000, args.interval, per_device_metrics=False)
                    ],
                    "firehose_if_batched": {
                        "10": firehose_if_batched(10, args.interval),
                        "1000": firehose_if_batched(1000, args.interval),
                    },
                },
                indent=2,
            )
        )
    else:
        print(f"Monthly estimate, one message every {args.interval} s per device, US East prices\n")
        print(markdown(args.interval))
        print(
            f"\nFirehose line if records were packed to 5 KB (not implemented): "
            f"${firehose_if_batched(10, args.interval):,.2f} / "
            f"${firehose_if_batched(1000, args.interval):,.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
