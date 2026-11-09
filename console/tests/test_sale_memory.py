"""Large sales finalize and recover without materializing acknowledgement payloads."""

import hashlib
import json
import tracemalloc
from types import SimpleNamespace

import pytest
from streamscale_console_worker import sale as worker
from streamscale_console_worker.inspection import Inspection


def fake_external_io(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            pass

    monkeypatch.setattr(worker.psycopg, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(worker, "offsets", lambda *args: {str(p): 0 for p in range(12)})


def run_record(attempts):
    return {
        "artifact_version": 1,
        "profile": "console",
        "status": "publishing",
        "run_id": "12345678-1234-5678-1234-567812345678",
        "attempts": attempts,
        "start_offsets": {str(p): 0 for p in range(12)},
        "console": {},
    }


def test_finalization_keeps_exact_counts_hash_and_bounded_memory(tmp_path, monkeypatch):
    fake_external_io(monkeypatch)
    settings = SimpleNamespace(kafka_bootstrap_servers="unused", database_url="unused")
    sale = worker.Sale(settings, {}, root=tmp_path)
    sale.directory.mkdir(parents=True)
    count = 20_000
    sale.run = run_record(count)
    sale.cursor = count
    path = sale.directory / "acknowledgements.jsonl"
    line = json.dumps({"event_id": "event", "payload_base64": "x" * 2048}).encode() + b"\n"
    expected = hashlib.sha256()
    with path.open("wb") as stream:
        for _ in range(count):
            stream.write(line)
            expected.update(line)
        stream.write(b'{"unfinished":')
    tracemalloc.start()
    try:
        sale.finalize("completed")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * 1024**2
    assert sale.run["acknowledged"] == count
    assert sale.run["acknowledgements_sha256"] == expected.hexdigest()
    assert sale.run["console"]["outcome"] == "completed"
    assert sale.run["console"]["unsent_attempts"] == 0
    assert path.stat().st_size == count * len(line)


@pytest.mark.parametrize("prefix", [b"", b"{}\n", b"{}\n\n"])
def test_partial_tail_truncation_preserves_all_complete_bytes(tmp_path, prefix):
    path = tmp_path / "acks.jsonl"
    path.write_bytes(prefix + b"x" * 200_000)
    worker._drop_partial_line(path)
    assert path.read_bytes() == prefix
    worker._drop_partial_line(path)
    assert path.read_bytes() == prefix


def test_recovery_uses_durable_budget_without_loading_a_manifest(tmp_path, monkeypatch):
    fake_external_io(monkeypatch)
    directory = tmp_path / "runs" / "sale-test"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(run_record(100)))
    (directory / "acknowledgements.jsonl").write_bytes(b'{}\n\n{}\n{"partial":')
    worker.control.set_current_run("sale-test", tmp_path)
    settings = SimpleNamespace(kafka_bootstrap_servers="unused", database_url="unused")
    result = worker.recover_interrupted(settings, tmp_path)
    assert result["attempts"] == 100
    assert result["acknowledged"] == 2
    assert result["console"]["attempted"] == 2
    assert result["console"]["unsent_attempts"] == 98
    assert result["console"]["outcome"] == "interrupted"


def test_finished_manifest_is_released_without_losing_status_budget(tmp_path, monkeypatch):
    sale = worker.Sale(SimpleNamespace(), {}, root=tmp_path)
    sale.run = run_record(2)
    sale.attempts = [{"scheduled_at_us": 0}, {"scheduled_at_us": 10}]
    sale.pacer = worker.Pacer(sale.attempts)
    sale.cursor = 2
    monkeypatch.setattr(sale, "_publish", lambda: None)
    sale._run()
    assert sale.attempts == sale.pacer.attempts == []
    assert sale.status()["budget"] == 2


def test_corrupt_complete_acknowledgement_is_rejected(tmp_path):
    path = tmp_path / "acks.jsonl"
    path.write_bytes(b'{}\n{"corrupt":\n{}\n')
    with pytest.raises(json.JSONDecodeError):
        worker._acknowledgement_summary(path)


def test_inspection_streams_and_ignores_only_the_incomplete_tail(tmp_path):
    path = tmp_path / "acknowledgements.jsonl"
    path.write_bytes(b'{"event_id":"a"}\n\n{"event_id":"b"}\n{"partial":')
    inspector = Inspection.__new__(Inspection)
    rows = inspector._acknowledgements({"run_dir": str(tmp_path)})
    assert iter(rows) is rows
    assert list(rows) == [{"event_id": "a"}, {"event_id": "b"}]
    path.write_bytes(b'{}\n{"corrupt":\n{}\n')
    with pytest.raises(json.JSONDecodeError):
        list(inspector._acknowledgements({"run_dir": str(tmp_path)}))
