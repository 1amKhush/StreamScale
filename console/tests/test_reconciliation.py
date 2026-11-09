"""Source/SQL/DLQ proof invariants and bounded memory for expanded workloads."""

import json
import tracemalloc
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from streamscale_console_worker import reconcile as worker

from streamscale.contract import ContractError

RUN = "12345678-1234-5678-1234-567812345678"
WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def event(identity, when=0, kind="ORDER_CREATED", order="order"):
    return {
        "event_id": identity,
        "order_id": order,
        "run_id": RUN,
        "event_time": f"2026-01-01T00:00:{when:02d}Z",
        "event_type": kind,
        "order_amount": "10.00",
    }


def configure(tmp_path, monkeypatch, records, facts, states, acks, ends=None):
    ends = ends or {str(p): (6 if p == 11 else 1 if p == 10 else 0) for p in range(12)}
    run = {
        "artifact_version": 1,
        "status": "published",
        "run_id": RUN,
        "group_id": "group",
        "start_offsets": {str(p): 0 for p in range(12)},
        "end_offsets": ends,
        "dlq_start_offsets": {str(p): 0 for p in range(12)},
        "attempts": 7,
        "console": {"outcome": "completed"},
    }
    (tmp_path / "run.json").write_text(json.dumps(run))
    with (tmp_path / "acknowledgements.jsonl").open("w") as stream:
        for row in acks:
            stream.write(json.dumps(row) + "\n")
        stream.write('{"partial":')

    def validate(value, key):
        if isinstance(value, Exception):
            raise value
        return value

    def source(bootstrap, topic, starts, ends):
        if topic == worker.INPUT_TOPIC:
            return records()
        envelope = json.dumps(
            {"source": {"topic": worker.INPUT_TOPIC, "partition": 10, "offset": 0}}
        ).encode()
        return iter([(0, 0, None, envelope), (0, 1, None, envelope)])

    class Cursor:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            pass

        def __iter__(self):
            return iter(facts() if self.name == "reconcile_facts" else states())

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self, name):
            return Cursor(name)

        def execute(self, *args):
            return SimpleNamespace(fetchone=lambda: (WHEN,))

    class Group:
        def __init__(self, *args):
            pass

        def committed(self, parts, **kwargs):
            return [
                SimpleNamespace(partition=p.partition, offset=ends[str(p.partition)]) for p in parts
            ]

        def close(self):
            pass

    monkeypatch.setattr(worker, "read_range", source)
    monkeypatch.setattr(worker, "validate_event", validate)
    monkeypatch.setattr(worker.psycopg, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(worker, "offsets", lambda *a: {str(p): 1 for p in range(12)})
    monkeypatch.setattr(worker, "Consumer", Group)
    monkeypatch.setattr(worker.tempfile, "tempdir", str(tmp_path))
    return SimpleNamespace(kafka_bootstrap_servers="unused", database_url="unused")


@pytest.mark.parametrize("broken", [False, True])
def test_streamed_proof_preserves_duplicate_late_tie_dlq_and_missing_checks(
    tmp_path, monkeypatch, broken
):
    rows = [
        (11, 0, None, event("a")),
        (11, 1, None, event("b", 3, "PAYMENT_CONFIRMED")),
        (11, 2, None, event("c", 1)),  # late: cannot roll state backward
        (11, 3, None, event("d", 3, "ORDER_CANCELLED")),  # equal time, greater offset
        (11, 4, None, event("a")),
        (10, 0, None, ContractError("INVALID_JSON", "bad")),
    ]
    acks = [
        {
            "partition": p,
            "offset": o,
            "valid": not isinstance(v, Exception),
            "event_id": v.get("event_id") if isinstance(v, dict) else None,
        }
        for p, o, _, v in rows
    ]

    def facts():
        return [(name, i + 1) for i, name in enumerate("abc" if broken else "abcd")]

    def states():
        return [("order", WHEN.replace(second=3), 3, "d", 11, "CANCELLED", "10.00")]

    settings = configure(tmp_path, monkeypatch, lambda: iter(rows), facts, states, acks)
    result = worker.reconcile(tmp_path, settings)
    assert result["log_duplicates"] == 1 and result["valid_unique_in_log"] == 4
    assert result["state_regressions"] == 0 and result["state_rows"] == 1
    assert result["invalid_sources"] == result["dlq_unique_sources"] == 1
    assert result["dlq_copies"] == 2 and result["missing_dlq_sources"] == 0
    assert result["acknowledged"] == 6 and result["acknowledged_not_in_log"] == 0
    assert result["missing_acknowledged_count"] == int(broken)
    assert result["missing_acknowledged_ids"] == (["d"] if broken else [])
    assert result["status"] == ("failed" if broken else "passed")
    if not broken:
        assert result["latency_s"] == {
            "p50": 2,
            "p95": 4,
            "p99": 4,
            "max": 4,
            "count": 4,
            "target_seconds": 2,
            "above_target": 2,
            "source": "SQL ingested_at - produced_at for every stored fact",
        }
    assert not list(tmp_path.glob("streamscale-reconcile-*"))


def test_large_proof_does_not_retain_source_payloads_or_sql_rows(tmp_path, monkeypatch):
    size = 12_000

    def records():
        for i in range(size):
            # Distinct padding makes accidentally retaining decoded payloads measurable.
            yield 11, i, None, event(str(i), order=str(i)) | {"unused": "x" * 1000 + str(i)}

    def facts():
        return ((str(i), 1.25) for i in range(size))

    def states():
        return ((str(i), WHEN, i, str(i), 11, "CREATED", "10.00") for i in range(size))

    acks = ({"partition": 11, "offset": i, "valid": True, "event_id": str(i)} for i in range(size))
    settings = configure(
        tmp_path,
        monkeypatch,
        records,
        facts,
        states,
        acks,
        {str(p): size if p == 11 else 0 for p in range(12)},
    )
    tracemalloc.start()
    try:
        result = worker.reconcile(tmp_path, settings)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["status"] == "passed" and result["stored_facts"] == size
    assert result["latency_s"]["p95"] == 1.25
    assert peak < 8 * 1024**2, f"Retained {peak / 1024**2:.1f} MiB of Python allocations"
    assert not list(tmp_path.glob("streamscale-reconcile-*"))


def test_failed_read_preserves_prior_result_and_cleans_only_its_scratch(tmp_path, monkeypatch):
    def records():
        yield 11, 0, None, event("a")
        raise RuntimeError("source unavailable")

    settings = configure(tmp_path, monkeypatch, records, lambda: [], lambda: [], [])
    result = tmp_path / "console-reconciliation.json"
    result.write_text('{"prior": true}')
    unrelated = tmp_path / "keep.sqlite"
    unrelated.write_text("user artifact")
    with pytest.raises(RuntimeError, match="source unavailable"):
        worker.reconcile(tmp_path, settings)
    assert result.read_text() == '{"prior": true}' and unrelated.read_text() == "user artifact"
    assert not list(tmp_path.glob("streamscale-reconcile-*"))


def test_restart_cannot_satisfy_a_new_request_with_an_older_success(tmp_path):
    directory = tmp_path / "runs/sale-cache"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text("{}")
    (directory / "console-reconciliation.json").write_text(
        json.dumps({"status": "passed", "request_id": "old-request"})
    )
    reconciler = worker.Reconciler(SimpleNamespace(root=tmp_path, settings=None))
    assert reconciler.result({"run": "sale-cache", "request_id": "new-request"}) == {
        "state": "not-started"
    }
    assert (
        reconciler.result({"run": "sale-cache", "request_id": "old-request"})["state"] == "passed"
    )
    assert reconciler.result({"run": "sale-cache"})["state"] == "passed"
    reconciler.jobs["sale-cache"] = {"state": "running", "request_id": "old-request"}
    assert (
        reconciler.result({"run": "sale-cache", "request_id": "new-request"})["state"]
        == "not-started"
    )
