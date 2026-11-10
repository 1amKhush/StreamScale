"""Replica choices must compare backlog at the same future timestamp."""

import pytest

from streamscale.predictive import DEFAULT_POLICY
from streamscale.predictive.policy import recommend


@pytest.mark.parametrize("startup", [0, 12])
def test_slower_downscale_gets_no_extra_drain_time(startup):
    calibration = {
        "capacity": {n: {1: 100, 2: 200, 3: 360, 4: 400}.get(n, 300) for n in range(1, 13)},
        "actuation_seconds": startup,
    }
    row = {
        "lag": [1000] * 12,
        "ready": 4,
        "desired": 4,
        "stable": True,
        "assignments": [list(range(p, p + 3)) for p in range(0, 12, 3)],
    }
    result = recommend(
        row, [0] * 12, calibration, DEFAULT_POLICY | {"headroom": 0}, minimum=3, maximum=4
    )
    # Four consumers serve 400/12 events/s on each partition. At t+20,
    # 333 1/3 events remain: ten seconds of queue delay, with no feasible target.
    assert result["recommended"] == 4
    assert result["capacity_limited"]
    assert result["candidates"][1]["latency_proxy_s"] == pytest.approx(10)
    if startup == 12:
        # The slower target serves only the final eight seconds of that window:
        # (1000 - 12*(400/12) - 8*(360/12)) / (360/12) = 12 seconds.
        assert result["candidates"][0]["latency_proxy_s"] == pytest.approx(12)


def test_startup_longer_than_forecast_uses_one_common_planning_window():
    calibration = {
        "capacity": {n: {1: 100, 2: 200, 3: 360, 4: 400}.get(n, 300) for n in range(1, 13)},
        "actuation_seconds": 30,
    }
    row = {
        "lag": [0] * 12,
        "ready": 1,
        "desired": 1,
        "stable": True,
        "assignments": [list(range(12))],
    }
    result = recommend(
        row, [200 / 12] * 12, calibration, DEFAULT_POLICY | {"horizon": 10, "headroom": 0}
    )
    # All choices end at t+30. No new consumers can process during those
    # 30 seconds; 3,000 events accumulate, then four consumers serve 400/s.
    assert result["recommended"] == 4
    assert result["candidates"][3]["latency_proxy_s"] == pytest.approx(7.5)
    assert result["candidates"][0]["latency_proxy_s"] == pytest.approx(30)
    assert result["projection_seconds"] == 30
