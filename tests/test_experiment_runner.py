"""Experiment order, calibration and tampered evidence must not silently change."""

import hashlib
import json

import pytest

from experiments.run import ROOT, configuration, freeze, plan, validate_matrix, verify_evidence


def test_plan_has_every_cell_once_in_seeded_order():
    protocol = json.loads((ROOT / "experiments/protocol.json").read_text())
    cells = plan(protocol)
    assert cells == plan(protocol)
    assert len(cells) == len({cell["key"] for cell in cells}) == 27
    assert all(
        sum(cell["mode"] == mode for cell in cells) == 9
        for mode in ("fixed", "cpu-hpa", "lag-keda")
    )
    frozen = freeze(protocol, 91.5)
    assert frozen["rates"] == {"steady": 36, "surge": 274}
    assert all(
        sum(seconds for seconds, _ in scenario["schedule"]) == 60
        for scenario in frozen["scenarios"].values()
    )
    assert "rates" not in protocol
    assert configuration(frozen, "measured", [[60, 36]])["protocol_version"] == 2


def test_matrix_refuses_missing_failed_or_different_workloads():
    protocol = json.loads((ROOT / "experiments/protocol.json").read_text())
    cells = plan(protocol)
    rows = [
        dict(
            cell_key=cell["key"],
            phase="measured",
            performance_eligible=1,
            run_id=str(index),
            image_digest="same",
            logical_sha256=cell["scenario"],
            scenario=cell["scenario"],
        )
        for index, cell in enumerate(cells)
    ]
    state = dict(rows=rows, plan=cells, image={"image_id": "same"}, protocol=protocol)
    validate_matrix(state)
    rows[0]["performance_eligible"] = 0
    with pytest.raises(RuntimeError, match="failed"):
        validate_matrix(state)
    rows[0]["performance_eligible"] = 1
    rows[0]["logical_sha256"] = "different"
    with pytest.raises(RuntimeError, match="differ logically"):
        validate_matrix(state)


def test_reuse_refuses_changed_raw_evidence(tmp_path):
    directory = tmp_path / "campaign/run"
    directory.mkdir(parents=True)
    row = {"artifact_directory": str(directory)}
    for name in (
        "run.json",
        "manifest.jsonl",
        "acknowledgements.jsonl",
        "samples.jsonl",
        "sql-measurements.json",
        "before-restart-inspection.json",
        "restart.json",
    ):
        (directory / name).write_text("{}\n")
    (directory / "summary.json").write_text(json.dumps(row))
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()
    }
    (directory / "checksums.json").write_text(json.dumps(hashes))
    verify_evidence(directory.parent, [row])
    (directory / "samples.jsonl").write_text("tampered")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_evidence(directory.parent, [row])
