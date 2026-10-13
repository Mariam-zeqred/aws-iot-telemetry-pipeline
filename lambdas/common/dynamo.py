"""Helpers for the two things boto3's DynamoDB resource is fussy about: Decimal and JSON."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any


def to_item(record: dict[str, Any]) -> dict[str, Any]:
    """DynamoDB refuses float; round-trip through JSON parsing floats as Decimal."""
    return json.loads(json.dumps(record), parse_float=Decimal)


def from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Decimal back to int/float so the API can json.dumps it."""

    def conv(v: Any) -> Any:
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return conv(item)
