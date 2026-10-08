from datetime import UTC, datetime
from uuid import uuid4

import pytest
from streamscale_workload.generator import generate, logical_workload, schedule_us

from experiments.metrics import integral, nearest_rank, quantity, recovery, summarize


def instant(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def test_empirical_percentile_uses_nearest_rank_without_interpolation():
    assert nearest_rank(list(range(1, 21))) == 19
    assert nearest_rank([4]) == 4
    assert nearest_rank([]) is None


def test_replica_integral_uses_actual_intervals_and_partial_window_edges():
    samples = [{"time": 0, "ready": 1}, {"time": 10, "ready": 3}, {"time": 20, "ready": 1}]
    assert integral(samples, "ready", 5, 25) == (40, 20)
    assert integral(samples[1:], "ready", 5, 25) == (35, 15)


@pytest.mark.parametrize(
    "text,kind,expected",
    [("150m", "cpu", 0.15), ("2000000n", "cpu", 0.002), ("8Mi", "memory", 8388608)],
)
def test_kubernetes_resource_units(text, kind, expected):
    assert quantity(text, kind) == expected


def test_recovery_needs_five_distinct_fresh_zero_samples_after_transition():
    samples = [
        {"time": second, "lag": {"0": 0}, "exporter_sample_at": second} for second in range(10)
    ]
    assert recovery(samples, 3) == 4
    repeated = [sample | {"exporter_sample_at": 0} for sample in samples]
    assert recovery(repeated, 3) is None
    interrupted = samples[:5] + [samples[5] | {"lag": {"0": 10}}] + samples[6:]
    assert recovery(interrupted, 3) is None


def test_custom_schedule_preserves_fault_sequence_across_modes_and_ids(tmp_path):
    schedule = [[2, 100], [2, 200]]
    first = generate(tmp_path / "a", orders=300, seed=101, profile="correctness", schedule=schedule)
    second = generate(
        tmp_path / "b",
        orders=300,
        seed=101,
        profile="correctness",
        schedule=schedule,
        mode="lag-keda",
    )
    assert first["logical_sha256"] == second["logical_sha256"]
    assert first["manifest_sha256"] != second["manifest_sha256"]
    assert first["publishing_schedule"] == schedule
    assert schedule_us(200, "steady", schedule) == 2_000_000


@pytest.mark.parametrize("schedule", [[], [[0, 100]], [[2, False]], [[1, -1]], [[1, 2, 3]]])
def test_invalid_schedules_fail_before_creating_artifacts(tmp_path, schedule):
    with pytest.raises(ValueError):
        generate(tmp_path / "run", schedule=schedule)
    assert not (tmp_path / "run").exists()


def test_scheduling_refuses_to_truncate_an_oversized_workload():
    with pytest.raises(ValueError, match="publishing window"):
        logical_workload(42, 10, "steady", "normal", uuid4(), [[1, 10]])


def test_throughput_half_open_window_and_failed_run_exclusion():
    run = {
        "publish_started_at": instant(100),
        "publish_ended_at": instant(110),
        "publishing_schedule": [[10, 2]],
        "run_id": "run",
        "mode": "fixed",
        "scenario": "steady",
        "seed": 101,
        "logical_sha256": "logical",
        "image": "same-image",
        "git_commit": "revision",
        "git_dirty": False,
        "acknowledged": 3,
    }
    measurements = {
        "ended_at": instant(111),
        "facts": [
            {"event_id": str(index), "produced_at": instant(100), "ingested_at": instant(at)}
            for index, at in enumerate((100, 109, 110))
        ],
    }
    samples = [
        {
            "time": second,
            "lag": {"0": 0, "1": 0, "2": 0},
            "ready": 1,
            "cpu_cores": 0.1,
            "memory_bytes": 1024,
            "exporter_sample_at": second,
        }
        for second in range(100, 112)
    ]
    inspection = {
        "status": "passed",
        "missing_valid_ids": [],
        "unexpected_valid_ids": [],
        "duplicate_stored_ids": 0,
        "state_regressions": 0,
        "missing_dlq_sources": 0,
        "invalid_dlq_envelopes": 0,
        "remaining_run_lag": {"0": 0},
        "stored_facts": 3,
        "duplicate_attempts": 0,
        "invalid_attempts": 0,
        "dlq_copies": 0,
        "dlq_unique_sources": 0,
    }
    config = {"phase": "measured", "protocol_version": 2, "sample_interval_seconds": 1}
    row = summarize(run, measurements, samples, inspection, inspection, config)
    assert row["throughput_window_per_second"] == 0.2  # The fact at the upper boundary is excluded.
    assert row["performance_eligible"] == 1
    assert row["p95_end_to_end_seconds"] == 10
    failed = summarize(
        run, measurements, samples, inspection, inspection | {"missing_valid_ids": ["lost"]}, config
    )
    assert failed["performance_eligible"] == 0
    assert failed["status"] == "failed"
