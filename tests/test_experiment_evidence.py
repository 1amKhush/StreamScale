"""Independent evidence checks catch changed SQL results and actual pod controls."""

import hashlib
import json

import pytest

from experiments.evidence import audit


def put(path, value):
    path.write_text(json.dumps(value))


def hashes(directory):
    put(
        directory / "checksums.json",
        {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.iterdir()
            if path.name != "checksums.json"
        },
    )


@pytest.fixture
def campaign(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    row = {
        "run_id": "one",
        "mode": "fixed",
        "scenario": "steady",
        "repetition": 1,
        "phase": "measured",
        "status": "passed",
        "performance_eligible": 1,
        "artifact_directory": str(directory),
        "image_digest": "digest",
        "unique_facts": 1,
        "p95_end_to_end_seconds": 0.5,
        "throughput_window_per_second": 0.1,
        "measurement_start": "2026-10-08T00:00:00Z",
        "measurement_end": "2026-10-08T00:00:10Z",
    }
    put(
        tmp_path / "campaign.json",
        {
            "rows": [row],
            "failures": [],
            "status": "running",
            "image": {"image": "streamscale:one", "image_id": "digest"},
        },
    )
    put(directory / "summary.json", row)
    put(
        directory / "run.json",
        {
            "run_id": "one",
            "group_id": "group",
            "image": "streamscale:one",
            "logical_sha256": "logical",
        },
    )
    put(
        directory / "sql-measurements.json",
        {
            "run_id": "one",
            "facts": [
                {
                    "event_id": "event",
                    "produced_at": "2026-10-08T00:00:01Z",
                    "ingested_at": "2026-10-08T00:00:01.500000Z",
                }
            ],
        },
    )
    inspection = {"status": "passed", "stored_facts": 1}
    put(directory / "before-restart-inspection.json", inspection)
    put(directory / "restart.json", {"inspection": inspection})
    pod = {
        "spec": {"containers": [{"image": "streamscale:one", "resources": {"cpu": "100m"}}]},
        "status": {"containerStatuses": [{"imageID": "actual-digest"}]},
    }
    sample = {"mode": "fixed", "desired": 1, "pods": [pod]}
    (directory / "samples.jsonl").write_text(json.dumps(sample) + "\n")
    hashes(directory)
    return tmp_path, directory


def test_audit_recomputes_sql_metrics_and_observes_actual_controls(campaign):
    root, directory = campaign
    result = audit(root, complete=False)
    assert result["measured_runs"] == 1
    assert result["actual_consumer_image_ids"] == ["actual-digest"]
    sql = json.loads((directory / "sql-measurements.json").read_text())
    sql["facts"][0]["ingested_at"] = "2026-10-08T00:00:02Z"
    put(directory / "sql-measurements.json", sql)
    hashes(directory)
    with pytest.raises(RuntimeError, match="p95 does not match"):
        audit(root, complete=False)


def test_audit_rejects_changed_actual_consumer_image(campaign):
    root, directory = campaign
    sample = json.loads((directory / "samples.jsonl").read_text())
    sample["pods"][0]["spec"]["containers"][0]["image"] = "another:image"
    (directory / "samples.jsonl").write_text(json.dumps(sample) + "\n")
    hashes(directory)
    with pytest.raises(RuntimeError, match="Actual consumer pod"):
        audit(root, complete=False)


def test_partial_evidence_cannot_be_claimed_complete(campaign):
    with pytest.raises(RuntimeError, match="incomplete"):
        audit(campaign[0])
