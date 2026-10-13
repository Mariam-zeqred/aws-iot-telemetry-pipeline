import io
import json
import time

from simulator.incubator_model import IncubatorModel
from simulator.simulate import build_parser, publish_loop, replay_records, topic_for

from common.telemetry import validate


def test_model_reaches_and_holds_target() -> None:
    m = IncubatorModel(target=34.5, ambient=27.0, interval_s=15, seed=3)
    temps = [m.step()["temp"] for _ in range(4 * 60 * 4)]  # 4 hours
    assert temps[0] < 28
    settled = temps[-240:]  # last hour
    # The 210 s dead time makes a hysteresis switch overshoot by ~2 degC, as the real device
    # does (incubator-telemetry-analysis measured 1.4 degC on its first approach to 32 degC).
    assert all(31.5 < t < 37.5 for t in settled), (min(settled), max(settled))
    assert max(settled) - min(settled) > 0.3, "hysteresis control should cycle, not freeze"


def test_model_records_validate_against_schema() -> None:
    m = IncubatorModel(start_ts=int(time.time()))
    m.open_door(60)
    recs = [m.step() for _ in range(10)]
    assert recs[0]["door"] == "open" and recs[-1]["door"] == "shut"
    for r in recs:
        assert validate(r, now=int(time.time()) + 200)["ts"] == r["ts"]


def test_model_is_deterministic() -> None:
    a = [IncubatorModel(seed=7).step() for _ in range(3)]
    b = [IncubatorModel(seed=7).step() for _ in range(3)]
    assert a == b


def test_replay_shifts_timestamps_and_validates() -> None:
    recs = list(
        replay_records(__import__("pathlib").Path("simulator/sample_history.json"), count=0)
    )
    assert len(recs) == 240
    now = int(time.time())
    assert abs(recs[-1]["ts"] - now) < 5
    assert all("iso" not in r for r in recs)
    for r in recs:
        validate(r, now=now)


def test_dry_run_prints_json_lines() -> None:
    args = build_parser().parse_args(["--dry-run", "--count", "3", "--device-id", "hive-X"])
    out = io.StringIO()
    assert publish_loop(args, out=out) == 0
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["targetTemp"] == 34.5
    assert topic_for("hive-X") == "incubator/hive-X/telemetry"


def test_live_mode_needs_certs() -> None:
    args = build_parser().parse_args(["--count", "1", "--endpoint", "x"])
    assert publish_loop(args) == 2
