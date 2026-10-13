"""Tunables for the stack, read from cdk.json `context` (override with `cdk -c key=value`)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from constructs import Construct


@dataclass(frozen=True)
class PipelineConfig:
    project: str = "incubator"
    device_ids: tuple[str, ...] = ("hive-1C5A00",)
    ttl_days: int = 30
    heartbeat_period_minutes: int = 5
    heartbeat_missing_periods: int = 2
    alarm_email: str = ""
    certificate_arns: dict[str, str] = field(default_factory=dict)
    retain_data: bool = False
    metric_namespace: str = "Incubator/Telemetry"
    topic_prefix: str = "incubator"

    @classmethod
    def from_context(cls, scope: Construct) -> PipelineConfig:
        def get(key: str, default: Any) -> Any:
            value = scope.node.try_get_context(key)
            if value is None or value == "":
                return default
            if isinstance(default, bool) and isinstance(value, str):
                return value.lower() in ("1", "true", "yes")
            if isinstance(default, int) and not isinstance(default, bool):
                return int(value)
            if isinstance(default, dict) and isinstance(value, str):
                return json.loads(value)
            if isinstance(default, tuple):
                if isinstance(value, str):
                    value = [v for v in value.split(",") if v]
                return tuple(value)
            return value

        cfg = cls(
            project=get("project", "incubator"),
            device_ids=get("device_ids", ("hive-1C5A00",)),
            ttl_days=get("ttl_days", 30),
            heartbeat_period_minutes=get("heartbeat_period_minutes", 5),
            heartbeat_missing_periods=get("heartbeat_missing_periods", 2),
            alarm_email=get("alarm_email", ""),
            certificate_arns=get("certificate_arns", {}),
            retain_data=get("retain_data", False),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not 1 <= self.ttl_days <= 365:
            raise ValueError("ttl_days must be 1..365")
        if self.heartbeat_period_minutes < 1 or self.heartbeat_missing_periods < 1:
            raise ValueError("heartbeat settings must be >= 1")
        for dev in self.device_ids:
            if not dev.replace("-", "").replace("_", "").isalnum() or len(dev) > 64:
                raise ValueError(f"bad device id {dev!r}")
        for dev in self.certificate_arns:
            if dev not in self.device_ids:
                raise ValueError(f"certificate for unknown device {dev!r}")
