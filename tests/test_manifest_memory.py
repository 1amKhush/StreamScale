"""Consumer startup verifies the publisher's unchanged digest with bounded memory."""

import hashlib
import json
import tracemalloc
from types import SimpleNamespace

import pytest
from streamscale_workload.generator import generate

from streamscale.run_artifacts import manifest_hash, verify_manifest_checksum


@pytest.mark.parametrize("profile", ["normal", "correctness", "fixtures"])
def test_streamed_checksum_matches_real_publisher_manifest(tmp_path, profile):
    directory = tmp_path / "run"
    run = generate(directory, seed=7, orders=2 if profile == "fixtures" else 40, profile=profile)
    assert verify_manifest_checksum(directory, run) is None
    path = directory / "manifest.jsonl"
    path.write_text(path.read_text().replace('"none"', '"late"', 1))
    with pytest.raises(RuntimeError, match="checksum"):
        verify_manifest_checksum(directory, run)


def test_canonical_checksum_ignores_object_key_order_and_empty_lines(tmp_path):
    attempts = [{"z": "héllo", "a": {"y": [1, 2], "x": None}}, {"i": 2, "b": True}]
    path = tmp_path / "manifest.jsonl"
    path.write_text("\n" + "\n\n".join(json.dumps(row) for row in attempts) + "\n")
    verify_manifest_checksum(tmp_path, {"manifest_sha256": manifest_hash(attempts)})
    path.write_text("")
    verify_manifest_checksum(tmp_path, {"manifest_sha256": manifest_hash([])})


def test_consumer_startup_checks_the_manifest_without_loading_attempts(tmp_path, monkeypatch):
    import streamscale.consumer as consumer
    import streamscale.run_artifacts as artifacts

    directory = tmp_path / "run"
    run = generate(directory, orders=40)
    run["status"] = "published"
    artifacts.write_json(directory / "run.json", run)

    def forbidden(*args, **kwargs):
        raise AssertionError("Consumer startup must not load the attempt list")

    class VerifiedBeforeConnect(Exception):
        pass

    def before_connect(*args):
        raise VerifiedBeforeConnect

    monkeypatch.setattr(artifacts, "read_jsonl", forbidden)
    monkeypatch.setattr(consumer, "ensure_topics", before_connect)
    with pytest.raises(VerifiedBeforeConnect):
        consumer.consume(SimpleNamespace(kafka_bootstrap_servers="unused"), directory=directory)


@pytest.mark.parametrize("payload", ['{"a": 1}\n{"broken":', "[]\n", "   \n"])
def test_corrupt_complete_or_partial_manifest_is_rejected(tmp_path, payload):
    (tmp_path / "manifest.jsonl").write_text(payload)
    with pytest.raises(ValueError):
        verify_manifest_checksum(tmp_path, {"manifest_sha256": manifest_hash([])})


def test_large_consumer_manifest_verification_does_not_load_the_file(tmp_path, monkeypatch):
    row = {"attempt_index": 1, "payload": "x" * 1024, "nested": {"value": 2}}
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "manifest.jsonl"
    expected = hashlib.sha256(b"[")
    with path.open("wb") as output:
        for index in range(40_000):
            output.write(canonical + b"\n")
            expected.update((b"," if index else b"") + canonical)
    expected.update(b"]")
    assert path.stat().st_size > 40 * 2**20

    def forbidden(*args, **kwargs):
        raise AssertionError("Consumer checksum verification must stream the manifest")

    monkeypatch.setattr(type(path), "read_text", forbidden)
    monkeypatch.setattr(type(path), "read_bytes", forbidden)
    tracemalloc.start()
    try:
        verify_manifest_checksum(tmp_path, {"manifest_sha256": expected.hexdigest()})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * 2**20
