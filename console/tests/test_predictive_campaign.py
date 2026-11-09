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


def test_preparation_refuses_insufficient_space_before_live_captures(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import pytest

    from experiments.predictive.campaign import prepare

    monkeypatch.setattr(
        "experiments.predictive.campaign.shutil.disk_usage", lambda path: SimpleNamespace(free=100)
    )
    with pytest.raises(RuntimeError, match="5 GiB"):
        asyncio.run(prepare(None, tmp_path, "identity"))
    assert not list(tmp_path.iterdir())


def test_overload_has_observed_warmup_and_a_bounded_surge_before_recovery():
    phases = config("overload-recovery", 42, base=85)["schedule"]
    assert sum(seconds for seconds, _ in phases) == 180
    assert phases[0][0] == 60
    assert sum(seconds for seconds, rate in phases if rate == max(r for _, r in phases)) == 30
    assert phases[-1][0] == 60
    assert all(1 <= rate <= 2000 for _, rate in phases)


def test_recovery_begins_after_the_last_peak_not_only_the_last_low_phase():
    run = {
        "id": "recovery",
        "family": "overload-recovery",
        "config": config("overload-recovery", 42),
        "points": [
            {"t": 0, "ready": 1, "lag_total": 100},
            {"t": 90, "ready": 1, "lag_total": 10},
            {"t": 100, "ready": 1, "lag_total": 0},
        ],
        "record": {"reconciliation_status": "passed", "reconciliation": {"latency_s": {"p95": 3}}},
    }
    assert summary(run)["recovery_seconds"] == 10
    assert summary(run)["correct"] and not summary(run)["target_met"]
