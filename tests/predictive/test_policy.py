import random
import time

import pytest

from streamscale.predictive.policy import project, recommend
from streamscale.topics import MAX_REPLICAS, PARTITIONS


def row():
    return {
        "lag": [0] * PARTITIONS,
        "ready": 1,
        "desired": 1,
        "assignments": [list(range(PARTITIONS))],
        "stable": True,
    }


def calibration():
    return {"capacity": {n: 120 * n for n in range(1, MAX_REPLICAS + 1)}, "actuation_seconds": 5}


def test_skew_cannot_be_fixed_by_assuming_equal_partition_traffic():
    current = row()
    current["lag"][0] = 100
    result = recommend(current, [2000] + [0] * (PARTITIONS - 1), calibration())
    assert result["capacity_limited"]
    assert all(not c["feasible"] for c in result["candidates"])


def test_quiet_workload_chooses_one_and_demand_can_require_more_than_three():
    assert recommend(row(), [1] * PARTITIONS, calibration())["recommended"] == 1
    result = recommend(row(), [40] * PARTITIONS, calibration())
    assert not result["capacity_limited"]
    assert result["recommended"] == 6
    assert [c["replicas"] for c in result["candidates"]] == list(range(1, 13))
    result = recommend(row(), [1000] * PARTITIONS, calibration(), minimum=2, maximum=10)
    assert result["capacity_limited"] and 2 <= result["recommended"] <= 10


def test_shared_database_plateau_is_not_extrapolated_as_linear_capacity():
    measured = calibration()
    measured["capacity"] = {n: min(n * 120, 300) for n in range(1, 13)}
    assert recommend(row(), [40] * PARTITIONS, measured)["capacity_limited"]


def test_missing_calibration_and_incomplete_assignment_hold_instead_of_guessing():
    measured = calibration()
    del measured["capacity"][7]
    with pytest.raises(ValueError, match="every replica count"):
        recommend(row(), [1] * PARTITIONS, measured)
    current = row() | {"assignments": [[0, 1, 2]]}
    with pytest.raises(ValueError, match="Incomplete partition assignments"):
        recommend(current, [1] * PARTITIONS, calibration())


def test_rebalance_bound_covers_balanced_assignments_including_uneven_groups():
    rng = random.Random(42)
    for n in range(1, 13):
        for _ in range(30):
            partitions = rng.sample(range(PARTITIONS), PARTITIONS)
            assigned = [partitions[i::n] for i in range(n)]
            lag = [rng.uniform(0, 500) for _ in partitions]
            rates = [rng.uniform(0, 50) for _ in partitions]
            bound, bound_saturated = project(lag, rates, 120 * n, n, 20)
            actual, actual_saturated = project(lag, rates, 120 * n, n, 20, assigned)
            assert bound >= actual
            assert not actual_saturated or bound_saturated


def test_twelve_target_decisions_fit_easily_inside_the_five_second_tick():
    started = time.monotonic()
    for _ in range(100):
        recommend(row(), [40] * PARTITIONS, calibration())
    assert time.monotonic() - started < 2


def test_delayed_readiness_does_not_credit_new_consumers_before_they_are_ready():
    measured = calibration()
    fast = recommend(row(), [40] * PARTITIONS, measured)
    delayed = recommend(row(), [40] * PARTITIONS, measured | {"actuation_seconds": 60})
    assert delayed["candidates"][5]["latency_proxy_s"] >= fast["candidates"][5]["latency_proxy_s"]


def test_infeasible_demand_chooses_best_measured_capacity_instead_of_always_twelve():
    measured = calibration()
    measured["capacity"] = {n: (720 if n == 6 else 120) for n in range(1, 13)}
    result = recommend(row(), [1000] * PARTITIONS, measured)
    assert result["capacity_limited"]
    assert result["recommended"] == 6
    assert all(not c["feasible"] for c in result["candidates"])
