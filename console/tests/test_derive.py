"""Derived chart values: reset-safe totals, histogram estimates and honest unknowns."""

import math

import pytest
from streamscale_console.derive import Accumulator, Deriver, drain_estimate, quantile, signals

BOUNDS = (0.01, 0.1, 1.0, math.inf)


def histogram(counts):
    cumulative, total = [], 0
    for bound, count in zip(BOUNDS, counts, strict=True):
        total += count
        cumulative.append([bound, total])
    return {"buckets": cumulative, "sum": 0.0, "count": total}


def consumer(pod, run_id, accepted=0, start=1.0, e2e=(0, 0, 0, 0), cpu=0.0, ok=True):
    return {
        "pod": pod,
        "ok": ok,
        "state": {"run_id": run_id, "state": "consuming", "assigned": [0]},
        "metrics": {
            "accepted": accepted,
            "duplicate": 0,
            "invalid": 0,
            "dlq": 0,
            "late": 0,
            "failed": {"db": 0},
            "e2e": histogram(e2e),
            "db": histogram((0, 0, 0, 0)),
            "cpu_seconds": cpu,
            "process_start": start,
            "rss_bytes": 50e6,
        },
    }


def sample(at, high, position, consumers=(), run_id="run", members=1, facts=None):
    return {
        "sampled_at": at,
        "sequence": int(at),
        "sources": {"kafka": {"ok": True, "at": at}},
        "run": {"run_id": run_id},
        "kafka": {
            "partitions": [
                {
                    "partition": part,
                    "high": high[part],
                    "position": position[part],
                    "lag": high[part] - position[part],
                }
                for part in range(3)
            ],
            "group": {"members": [{"partitions": [0, 1, 2]}] * members, "state": "Stable"},
        },
        "sql": {"facts": facts, "probe_ms": 1.0},
        "kubernetes": {
            "consumer": {"desired": 1, "ready": 1},
            "pods": [{"name": row["pod"], "cpu_limit": "500m", "restarts": 0} for row in consumers],
        },
        "consumers": list(consumers),
    }


def test_quantile_interpolates_like_prometheus():
    buckets = [(0.1, 50), (0.5, 90), (1.0, 100), (math.inf, 100)]
    assert quantile(0.5, buckets) == pytest.approx(0.1)
    assert quantile(0.95, buckets) == pytest.approx(0.75)
    assert quantile(0.95, []) is None
    assert quantile(0.95, [(1.0, 0), (math.inf, 0)]) is None


def test_quantile_in_the_infinite_bucket_reports_the_last_finite_bound():
    assert quantile(0.99, [(1.0, 10), (math.inf, 100)]) == 1.0


def test_restart_resets_are_added_not_subtracted():
    totals = Accumulator()
    totals.add([consumer("a", "run", accepted=100, start=1)], "run")
    totals.add([consumer("a", "run", accepted=150, start=1)], "run")
    # The process restarted: its counter starts from zero again.
    totals.add([consumer("a", "run", accepted=20, start=2)], "run")
    assert totals.snapshot()["totals"]["accepted"] == 170


def test_scaled_down_pods_keep_their_contribution():
    totals = Accumulator()
    totals.add([consumer("a", "run", accepted=10), consumer("b", "run", accepted=5)], "run")
    totals.add([consumer("a", "run", accepted=12)], "run")
    assert totals.snapshot()["totals"]["accepted"] == 17


def test_a_new_run_starts_new_totals_and_ignores_other_runs():
    totals = Accumulator()
    totals.add([consumer("a", "one", accepted=10)], "one")
    totals.add([consumer("a", "two", accepted=3), consumer("b", "one", accepted=50)], "two")
    assert totals.snapshot()["totals"]["accepted"] == 3


def test_failed_scrapes_contribute_nothing():
    totals = Accumulator()
    totals.add([consumer("a", "run", accepted=10, ok=False)], "run")
    assert totals.snapshot()["totals"]["accepted"] == 0


def test_live_p95_uses_only_the_recent_window():
    deriver = Deriver()
    early = consumer("a", "run", e2e=(0, 0, 100, 0))
    deriver.point(sample(0, [0] * 3, [0] * 3, [early]), 0)
    late = consumer("a", "run", e2e=(100, 0, 100, 0))
    point = deriver.point(sample(30, [0] * 3, [0] * 3, [late]), 30)
    # Only the newest 100 observations (all under 10 ms) fall inside the 15 s window.
    assert point["e2e_p95"] <= 0.01


def test_rates_lag_and_cpu_come_from_consecutive_samples():
    deriver = Deriver()
    first = deriver.point(
        sample(10, [100, 100, 100], [100, 100, 100], [consumer("a", "run", cpu=1.0)]), 10
    )
    assert first["rates"]["arrival"] is None
    second = deriver.point(
        sample(12, [300, 300, 300], [150, 150, 150], [consumer("a", "run", cpu=1.5)]), 12
    )
    assert second["rates"]["arrival"] == pytest.approx(300)
    assert second["rates"]["drain"] == pytest.approx(75)
    assert second["lag_total"] == 450
    # 0.5 CPU seconds over 2 seconds against a 500m limit is 50%.
    assert second["pods"][0]["cpu_pct"] == pytest.approx(50)


def test_unknown_lag_is_never_reported_as_zero():
    deriver = Deriver()
    broken = sample(1, [1, 1, 1], [0, 0, 0])
    broken["kafka"]["partitions"][1]["lag"] = None
    point = deriver.point(broken, 1)
    assert point["lag"] is None and point["lag_total"] is None
    missing = sample(2, [1, 1, 1], [0, 0, 0])
    missing["kafka"] = None
    point = deriver.point(missing, 2)
    assert point["lag_total"] is None and point["members"] is None


def test_drain_estimate_requires_net_progress():
    point = {"lag_total": 1000, "rates": {"drain": 300.0, "arrival": 100.0}}
    assert drain_estimate(point) == pytest.approx(5)
    assert drain_estimate(point | {"rates": {"drain": 100.0, "arrival": 120.0}}) is None
    assert drain_estimate(point | {"lag_total": 0}) == 0
    assert drain_estimate(point | {"lag_total": None}) is None


def test_signals_explain_backlog_and_missing_consumers_with_evidence():
    point = {
        "rates": {"arrival": 390.0, "drain": 130.0},
        "lag_total": 2000,
        "members": 0,
        "pods": [],
        "db_p95": None,
    }
    found = {row["id"]: row for row in signals(point, {"kubernetes": {}})}
    assert "no-consumers" in found and "backlog-growing" in found
    assert "390" in found["backlog-growing"]["evidence"]


def test_infrastructure_outage_is_critical():
    point = {
        "rates": {"arrival": None, "drain": None},
        "lag_total": None,
        "members": None,
        "pods": [],
        "db_p95": None,
    }
    infrastructure = {"postgres": {"desired": 0, "ready": 0}, "kafka": {"desired": 1, "ready": 1}}
    found = signals(point, {"kubernetes": {"infrastructure": infrastructure}})
    assert [row["id"] for row in found] == ["postgres-unavailable"]
    assert found[0]["severity"] == "critical"
