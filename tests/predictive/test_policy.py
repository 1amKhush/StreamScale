from streamscale.predictive.policy import recommend


def test_skew_cannot_be_fixed_by_assuming_equal_partition_traffic():
    row = {"lag": [100, 0, 0], "ready": 1, "desired": 1, "assignments": [[0, 1, 2]]}
    calibration = {"capacity": {1: 30, 2: 45, 3: 50}, "actuation_seconds": 5}
    result = recommend(row, [100, 0, 0], calibration)
    assert result["capacity_limited"]
    assert all(not c["feasible"] for c in result["candidates"])


def test_quiet_workload_chooses_smallest_capacity_and_overload_is_explicit():
    row = {"lag": [0, 0, 0], "ready": 1, "desired": 1, "assignments": [[0, 1, 2]]}
    calibration = {"capacity": {1: 30, 2: 45, 3: 50}, "actuation_seconds": 5}
    assert recommend(row, [1, 1, 1], calibration)["recommended"] == 1
    result = recommend(row, [100, 100, 100], calibration, minimum=2, maximum=3)
    assert result["capacity_limited"] and 2 <= result["recommended"] <= 3
