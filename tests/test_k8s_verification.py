"""Exercise asynchronous Prometheus scrape convergence without a live cluster."""

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def series(value, **labels):
    return {"metric": labels, "value": [1000, str(value)]}


def complete_samples():
    return {
        "streamscale_accepted_total": [series(396)],
        "streamscale_kafka_lag": [series(0, partition=str(part)) for part in range(3)],
        "streamscale_failed_total": [series(0)],
        "streamscale_exporter_last_success_unixtime": [series(101)],
    }


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "deploy/k8s"))
    module = importlib.import_module("verify")
    clock = SimpleNamespace(now=0, sleeps=0)

    def sleep(seconds):
        clock.now += seconds
        clock.sleeps += 1

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))

    def install(*snapshots):
        def query(endpoint, expression):
            assert endpoint == "http://prometheus"
            metric, selector = expression.split("{", 1)
            assert selector == 'run_id="current-run"}'
            snapshot = snapshots[min(clock.sleeps, len(snapshots) - 1)]
            return snapshot.get(metric, [])

        monkeypatch.setattr(module, "query", query)

    def wait(timeout=2):
        return module.wait_for_run_metrics(
            "http://prometheus",
            "current-run",
            {"streamscale_accepted_total": 396},
            fresh_after=100,
            timeout=timeout,
        )

    return install, wait, clock


def test_old_backlog_scrape_does_not_fail_a_drained_run(verifier):
    install, wait, clock = verifier
    stale = complete_samples()
    stale["streamscale_kafka_lag"][2] = series(60, partition="2")
    install(stale, complete_samples())
    samples = wait()
    assert clock.sleeps == 1
    assert sum(float(row["value"][1]) for row in samples["streamscale_kafka_lag"]) == 0


@pytest.mark.parametrize("reason", ["counter", "missing", "partial_lag", "freshness"])
def test_series_presence_alone_is_not_completed_run_evidence(verifier, reason):
    install, wait, clock = verifier
    stale = complete_samples()
    if reason == "counter":
        stale["streamscale_accepted_total"] = [series(180)]
    elif reason == "missing":
        stale["streamscale_accepted_total"] = []
    elif reason == "partial_lag":
        stale["streamscale_kafka_lag"] = [series(0, partition="0")]
    else:
        stale["streamscale_exporter_last_success_unixtime"] = [series(99)]
    install(stale, complete_samples())
    wait()
    assert clock.sleeps == 1


def test_persistent_backlog_fails_with_observed_lag_at_deadline(verifier):
    install, wait, clock = verifier
    pending = complete_samples()
    pending["streamscale_kafka_lag"][2] = series(60, partition="2")
    install(pending)
    with pytest.raises(RuntimeError, match="Kafka backlog did not drain: observed lag 60"):
        wait()
    assert clock.sleeps == 2


def test_missing_series_fails_with_metric_name_at_deadline(verifier):
    install, wait, _ = verifier
    pending = complete_samples()
    del pending["streamscale_accepted_total"]
    install(pending)
    with pytest.raises(RuntimeError, match="missing series: streamscale_accepted_total"):
        wait()


def test_handling_failures_are_not_retried_or_hidden(verifier):
    install, wait, clock = verifier
    failed = complete_samples()
    failed["streamscale_failed_total"] = [series(1)]
    install(failed, complete_samples())
    with pytest.raises(RuntimeError, match="cluster run had handling failures"):
        wait()
    assert clock.sleeps == 0
