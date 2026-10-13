import json

from cost import PRICES, estimate, main, markdown


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
    assert data["interval_s"] == 60 and len(data["1000"]) == 14
