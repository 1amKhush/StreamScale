"""Engine verdicts, evidence and lifecycle under application-independent failures."""

import copy
import json

import pytest

from streamscale.engine.__main__ import compare, main
from streamscale.engine.adapters import Memory
from streamscale.engine.model import validate
from streamscale.engine.runner import logical_plan, run


@pytest.fixture
def scenario():
    return {
        "version": 1,
        "seed": 7,
        "adapter": "streamscale.engine.adapters:Memory",
        "target": {"revision": "test", "environment": "memory-test-only"},
        "payloads": [{"temperature": 21}, {"temperature": -3}],
        "phases": [{"seconds": 0.03, "rate": 100}],
        "drain_seconds": 0.01,
        "poll_seconds": 0.002,
        "ack_timeout_seconds": 0.1,
        "max_lateness_seconds": 0.1,
    }


def test_fresh_ids_matched_inputs_and_preserved_evidence(scenario, tmp_path):
    a, b = [run(scenario, tmp_path / name) for name in ("a", "b")]
    assert a["status"] == b["status"] == "pass"
    assert a["logical_manifest_sha256"] == b["logical_manifest_sha256"]
    assert a["run_id"] != b["run_id"]
    records = [json.loads(p.read_text().splitlines()[0]) for p in tmp_path.glob("*/manifest.jsonl")]
    assert records[0]["id"] != records[1]["id"]
    assert a["checks"]["expected"] == 3
    assert a["checks"]["p95_seconds"] >= 0
    assert set(a["files_sha256"]) >= {
        "scenario.json",
        "logical-manifest.json",
        "manifest.jsonl",
        "acknowledgements.jsonl",
        "observations.jsonl",
    }
    assert [p["name"] for p in a["phases"]] == [
        "prepare",
        "load",
        "drain",
        "validation",
        "cleanup",
    ]
    with pytest.raises(FileExistsError):
        run(scenario, tmp_path / "a")


def test_loss_is_failure_even_with_all_inputs_acknowledged(scenario, tmp_path):
    scenario["options"] = {"drop": True}
    result = run(scenario, tmp_path / "loss")
    assert result["status"] == "fail"
    assert result["load"]["valid"]
    assert len(result["checks"]["missing"]) == 3
    assert result["checks"]["p95_seconds"] is None
    assert not result["eligible_for_comparison"]


def test_overloaded_generator_cannot_pass_or_rank(scenario, tmp_path):
    scenario["options"] = {"send_delay": 0.02}
    scenario["max_lateness_seconds"] = 0.001
    result = run(scenario, tmp_path / "overloaded")
    assert result["status"] == "inconclusive"
    assert result["checks"]["correct"]
    assert result["load"]["on_time_fraction"] < 0.95
    assert not result["eligible_for_comparison"]


@pytest.mark.parametrize("problem", ["duplicate", "unexpected", "wrong", "invalid-clock"])
def test_sink_contract_problems(scenario, tmp_path, monkeypatch, problem):
    class Broken(Memory):
        def observe(self, context):
            rows = copy.deepcopy(self.rows)
            if problem == "duplicate":
                rows.append(rows[0])
            elif problem == "unexpected":
                rows.append(dict(rows[0], id="unrelated-id"))
            elif problem == "wrong":
                rows[0]["value"] = {"wrong": True}
            else:
                rows[0]["completed_at"] = "1970-01-01T00:00:00+00:00"
            return rows

    monkeypatch.setattr("streamscale.engine.runner.symbol", lambda _: Broken)
    result = run(scenario, tmp_path / problem)
    assert result["status"] == ("inconclusive" if problem == "invalid-clock" else "fail")


def test_observes_past_first_match_to_catch_delayed_duplicate(scenario, tmp_path, monkeypatch):
    class DelayedDuplicate(Memory):
        observations = 0

        def observe(self, context):
            self.observations += 1
            rows = list(self.rows)
            return rows + rows[:1] if self.observations > 1 else rows

    monkeypatch.setattr("streamscale.engine.runner.symbol", lambda _: DelayedDuplicate)
    result = run(scenario, tmp_path / "late-duplicate")
    assert result["status"] == "fail"
    assert result["checks"]["duplicates"]


def test_preparation_failure_still_cleans_up_and_writes_error(scenario, tmp_path, monkeypatch):
    closed = []

    class Broken(Memory):
        def prepare(self, context):
            raise RuntimeError("password=must-not-be-recorded")

        def close(self):
            closed.append(True)

    monkeypatch.setattr("streamscale.engine.runner.symbol", lambda _: Broken)
    result = run(scenario, tmp_path / "error")
    assert result["status"] == "error"
    assert closed == [True]
    text = (tmp_path / "error/result.json").read_text()
    assert "password" not in text
    assert json.loads(text)["error_type"] == "RuntimeError"


def test_fault_fires_in_last_phase_tail_and_cleanup_runs(scenario, tmp_path):
    import sys

    marker = tmp_path / "markers"

    def command(text):
        return [
            sys.executable,
            "-c",
            f"from pathlib import Path; p=Path({str(marker)!r}); p.open('a').write({text!r})",
        ]

    scenario["phases"] = [{"seconds": 0.1, "rate": 10}]
    scenario["hooks"] = {
        "fault": {"at_seconds": 0.05, "argv": command("fault\n")},
        "cleanup": {"argv": command("cleanup\n")},
    }
    result = run(scenario, tmp_path / "fault")
    assert result["status"] == "pass"
    assert marker.read_text().splitlines() == ["fault", "cleanup"]
    assert "fault" in [p["name"] for p in result["phases"]]


def test_failing_fault_does_not_skip_cleanup(scenario, tmp_path):
    import sys

    marker = tmp_path / "cleanup"
    scenario["hooks"] = {
        "fault": {"at_seconds": 0, "argv": [sys.executable, "-c", "raise SystemExit(5)"]},
        "cleanup": {
            "argv": [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()",
            ]
        },
    }
    result = run(scenario, tmp_path / "fault-error")
    assert result["status"] == "error"
    assert marker.exists()


def test_failed_acknowledgement_is_inconclusive(scenario, tmp_path, monkeypatch):
    class Unconfirmed(Memory):
        def send(self, record, acknowledge):
            acknowledge({"id": record["id"], "error": "DeliveryError"})

    monkeypatch.setattr("streamscale.engine.runner.symbol", lambda _: Unconfirmed)
    result = run(scenario, tmp_path / "unconfirmed")
    assert result["status"] == "inconclusive"
    assert not result["load"]["delivery_valid"]


def test_warmup_is_separate_and_not_in_measured_counts(scenario, tmp_path):
    scenario["warmup_seconds"] = 0.02
    result = run(scenario, tmp_path / "warm")
    warm = json.loads((tmp_path / "warm/warmup/result.json").read_text())
    assert result["status"] == "pass"
    assert result["run_id"] != warm["run_id"]
    assert warm["load"]["scheduled"] == 2
    assert result["load"]["scheduled"] == 3


def test_comparison_retains_failed_runs_and_withholds_ranking(scenario, tmp_path):
    candidate = copy.deepcopy(scenario)
    candidate["options"] = {"drop": True}
    report = compare(scenario, candidate, tmp_path / "compare", repetitions=2)
    assert report["status"] == "fail"
    assert not report["performance_comparable"]
    assert report["summary"]["candidate"]["eligible"] == 0
    assert report["summary"]["candidate"]["median_p95_seconds"] is None
    assert len(report["runs"]) == 4
    assert len({r["logical_manifest_sha256"] for r in report["runs"]}) == 1
    assert len({r["run_id"] for r in report["runs"]}) == 4


def test_mismatched_comparison_rejected_before_execution(scenario, tmp_path):
    candidate = copy.deepcopy(scenario)
    candidate["phases"][0]["rate"] *= 2
    with pytest.raises(ValueError, match="must match"):
        compare(scenario, candidate, tmp_path / "mismatch")
    assert not (tmp_path / "mismatch").exists()


def test_invalid_config_and_ci_exit_status(scenario, tmp_path):
    scenario["phases"][0]["rate"] = float("nan")
    with pytest.raises(ValueError):
        validate(scenario)
    scenario["phases"][0]["rate"] = 100
    config_file = tmp_path / "scenario.json"
    config_file.write_text(json.dumps(scenario | {"options": {"drop": True}}))
    assert main(["run", str(config_file), "--output", str(tmp_path / "cli")]) == 1
    config_file.write_text("{broken")
    assert main(["run", str(config_file), "--output", str(tmp_path / "bad")]) == 2


def test_payload_order_reproducible_without_mutation(scenario):
    original = copy.deepcopy(scenario)
    assert logical_plan(scenario) == logical_plan(copy.deepcopy(scenario))
    assert scenario == original
