import json

import pytest

from cost import PRICES, estimate, firehose_if_batched, main, markdown


def test_every_price_has_a_source_page() -> None:
    for key, (price, unit, source) in PRICES.items():
        assert price > 0 and unit and source.startswith("https://aws.amazon.com/"), key


def test_cost_scales_with_fleet_and_rate(capsys) -> None:
    small = sum(line.usd for line in estimate(10, 15))
    big = sum(line.usd for line in estimate(1000, 15))
    slow = sum(line.usd for line in estimate(1000, 60))
    assert 0 < small < big and slow < big
    assert "Total per month" in markdown(15)
    assert main(["--json", "--interval", "60"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["interval_s"] == 60 and len(data["1000"]) == 15
    assert len(data["1000_fleet_only"]) == 15 and data["firehose_if_batched"]["1000"] > 0


def test_fleet_only_mode_removes_the_per_device_lines() -> None:
    per_device = {line.service: line.usd for line in estimate(1000, 15)}
    fleet = {line.service: line.usd for line in estimate(1000, 15, per_device_metrics=False)}
    assert fleet["CloudWatch metrics"] < per_device["CloudWatch metrics"] / 100
    assert fleet["CloudWatch alarms"] == pytest.approx(per_device["CloudWatch alarms"] - 100.0)
    unchanged = {k for k in fleet if fleet[k] == per_device[k]}
    assert {"IoT Core messaging", "DynamoDB writes (history + status)"} <= unchanged
    assert fleet["Heartbeat scan (Lambda + status-table reads)"] < 1  # the scan is cheap at 1 000
    assert sum(per_device.values()) - sum(fleet.values()) > 900


def test_firehose_batching_what_if_is_the_same_prices_on_real_bytes() -> None:
    table = {line.service: line.usd for line in estimate(1000, 15)}
    assert firehose_if_batched(1000, 15) < table["Firehose ingest + Parquet conversion"] / 10
