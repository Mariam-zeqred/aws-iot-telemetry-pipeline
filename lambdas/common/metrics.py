"""CloudWatch Embedded Metric Format (EMF).

Printing one JSON line per invocation makes CloudWatch extract metrics from the log stream.
It costs log ingestion only, no PutMetricData calls, and the Lambda needs no CloudWatch
permission for it. The alarms in the stack are built on these metrics. With a DeviceId
dimension each metric is published twice: once per device (for the per-device heartbeat
alarms) and once with no dimension (the fleet total). With no dimensions (the
`per_device_metrics = false` setting, or the heartbeat Lambda's fleet counts) only the
undimensioned series exists, so the metric count no longer grows with the fleet.
"""

from __future__ import annotations

import json
import time
from typing import Any


def emf_line(
    namespace: str, metrics: dict[str, float], dimensions: dict[str, str], **props: Any
) -> str:
    return json.dumps(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": namespace,
                        "Dimensions": [list(dimensions.keys()), []] if dimensions else [[]],
                        "Metrics": [{"Name": k, "Unit": "Count"} for k in metrics],
                    }
                ],
            },
            **dimensions,
            **metrics,
            **props,
        }
    )


def emit(
    namespace: str, metrics: dict[str, float], dimensions: dict[str, str], **props: Any
) -> None:
    print(emf_line(namespace, metrics, dimensions, **props))
