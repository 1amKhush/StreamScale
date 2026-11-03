from experiments.predictive.campaign import config, summary
from experiments.predictive.tuning import select
from streamscale.predictive import DEFAULT_POLICY


def test_infeasible_tuning_never_promotes_an_optimum():
    policy, evidence = select(
        [{"index": 0, "policy": DEFAULT_POLICY, "correct": True, "p95": 10, "replica_seconds": 1}]
    )
    assert policy == DEFAULT_POLICY and evidence["status"] == "infeasible"
    rows = [
        {
            "index": 1,
            "policy": DEFAULT_POLICY | {"horizon": 30},
            "correct": True,
            "p95": 1.8,
            "replica_seconds": 100,
        },
        {"index": 2, "policy": DEFAULT_POLICY, "correct": False, "p95": 1, "replica_seconds": 1},
    ]
    policy, evidence = select(rows)
    assert policy["horizon"] == 30 and evidence["selected"] == 1


def test_seeded_workloads_and_elapsed_time_cost():
    assert config("ramp", 42) == config("ramp", 42)
    assert config("ramp", 42) != config("ramp", 43)
    run = {
        "id": "x",
        "points": [{"t": 0, "ready": 2, "lag_total": 1}, {"t": 2, "ready": 2, "lag_total": 0}],
        "record": {
            "reconciliation_status": "passed",
            "reconciliation": {"latency_s": {"p95": 1.5}},
        },
    }
    assert summary(run)["replica_seconds"] == 4
