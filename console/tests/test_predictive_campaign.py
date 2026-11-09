import pytest

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


def complete_run():
    return {
        "id": "eligible-run",
        "mode": "ml-predictive",
        "config": {},
        "points": [
            {
                "t": t,
                "ready": 1,
                "lag_total": 0,
                "predictive": {"forecast_rate": 10, "model_id": "model"},
            }
            for t in (0, 2)
        ],
        "record": {
            "attempts": 10,
            "outcome": "completed",
            "comparable": True,
            "config": {"mode": "ml-predictive"},
            "reconciliation_status": "passed",
            "predictive_evidence": {"forecast_samples": 2},
            "reconciliation": {
                "status": "passed",
                "outcome": "completed",
                "acknowledged": 10,
                "latency_s": {"p95": 1.5},
            },
        },
    }


def test_complete_predictive_workload_is_eligible():
    from experiments.predictive.runtime import complete_workload

    run = complete_run()
    assert complete_workload(run["record"])
    assert summary(run)["performance_eligible"]


@pytest.mark.parametrize(
    "change", ["interrupted", "partial", "modified", "paused", "fault", "no_forecast"]
)
def test_correct_but_incomplete_or_modified_workload_is_not_a_performance_result(change):
    run = complete_run()
    record = run["record"]
    if change == "interrupted":
        record["outcome"] = "interrupted"
    elif change == "partial":
        record["reconciliation"]["acknowledged"] = 9
    elif change == "modified":
        record["segments"] = [{"mode": "manual"}]
    elif change == "paused":
        record["controls_changed"] = True
    elif change == "fault":
        record["faults"] = [{"kind": "postgres_outage"}]
    else:
        for point in run["points"]:
            point["predictive"]["forecast_rate"] = None
    result = summary(run)
    assert result["correct"] and result["target_met"]
    assert not result["performance_eligible"]


def test_cached_incomplete_capture_is_rejected_without_overwrite(tmp_path):
    import asyncio
    import json

    from experiments.predictive.campaign import retained

    run = complete_run()
    run["record"]["outcome"] = "interrupted"
    path = tmp_path / "trace.json"
    original = json.dumps(run)
    path.write_text(original)
    with pytest.raises(RuntimeError, match="incomplete or incompatible; preserved"):
        asyncio.run(retained(None, tmp_path, "trace", {}, "ml-predictive"))
    assert path.read_text() == original


def test_heldout_campaign_cannot_complete_with_an_ineligible_run(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from experiments.predictive import campaign

    run = complete_run()
    run["record"]["outcome"] = "interrupted"

    async def retained(*args, **kwargs):
        return run

    monkeypatch.setattr(
        campaign, "load", lambda *a: ("model", {}, {"calibration": {"capacity": {1: 10}}})
    )
    monkeypatch.setattr(campaign, "install", lambda *a: None)
    monkeypatch.setattr(campaign, "retained", retained)
    monkeypatch.setattr(
        campaign.shutil, "disk_usage", lambda *a: SimpleNamespace(free=10 * 1024**3)
    )
    with pytest.raises(RuntimeError, match="not performance eligible; preserved"):
        asyncio.run(campaign.evaluate(None, tmp_path))
    assert not (tmp_path / "evaluation/complete.json").exists()
