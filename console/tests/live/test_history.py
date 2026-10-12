"""M14 live acceptance: formal comparison, faithful replay and exports, untouched matrix."""

import hashlib
import json

import pytest
from streamscale_console.cluster import ARTIFACTS, ROOT

from .conftest import wait_idle

pytestmark = pytest.mark.live

MEASURED = sorted((ROOT / "docs/results/current").glob("*.csv")) + sorted(
    (ROOT / "docs/results/current").glob("*.json")
)


def digest(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


@pytest.fixture(scope="module")
def measured_before():
    return digest(MEASURED)


@pytest.fixture(scope="module")
def comparison(console_server, measured_before):
    console = console_server[2].client
    wait_idle(console)
    started = console.run("compare.start", {"preset": "flash-sale"}, timeout=60)
    assert started["state"] == "succeeded", started
    state = console.wait(
        lambda state: (
            (state.get("comparison") or {}).get("state") in {"completed", "failed", "cancelled"}
        ),
        timeout=1800,
        interval=5,
        describe="the formal comparison to finish",
    )
    return state["comparison"]


def test_a_formal_comparison_runs_each_mode_on_the_same_workload(console, comparison):
    assert comparison["state"] == "completed", comparison.get("error")
    assert [run["mode"] for run in comparison["runs"]] == ["fixed", "cpu-hpa", "lag-keda"]
    assert all(run["eligible"] for run in comparison["runs"]), comparison["runs"]
    assert all(run["exact_p95_s"] and run["stored_facts"] == 9781 for run in comparison["runs"])
    groups = console.get("/api/comparisons")["groups"]
    sessions = {run["session"] for run in comparison["runs"]}
    holding = [group for group in groups if sessions <= {row["id"] for row in group["sessions"]}]
    assert len(holding) == 1  # one logical workload for all three runs
    assert console.state()["sample"]["scaling"]["mode"] == "fixed"


def test_replay_and_exports_match_the_recorded_telemetry(console, comparison):
    session = comparison["runs"][0]["session"]
    detail = console.get(f"/api/sessions/{session}")
    recorded = (ARTIFACTS / "sessions" / session / "telemetry.jsonl").read_text().splitlines()
    assert len(detail["frames"]) == len([line for line in recorded if line.strip()]) > 60
    for frame, line in zip(detail["frames"], recorded, strict=False):
        assert frame == json.loads(line)
    # Each frame carries enough state to redraw the floor at that moment.
    for frame in detail["frames"][5:]:
        partitions = frame["sample"]["kafka"]["partitions"]
        if frame["point"]["lag"] is not None:
            assert [row["lag"] for row in partitions] == frame["point"]["lag"]

    with console.opener.open(
        console.url + f"/api/sessions/{session}/export/points.csv"
    ) as response:
        rows = response.read().decode().strip().splitlines()
    assert len(rows) == len(detail["frames"]) + 1


def test_interactive_sessions_are_excluded_from_rankings(console):
    groups = console.get("/api/comparisons")["groups"]
    excluded = [row for group in groups for row in group["sessions"] if not row["eligible"]]
    assert excluded and all(row["reasons"] for row in excluded)
    reasons = {reason for row in excluded for reason in row["reasons"]}
    assert reasons & {"faults injected", "scaling changed during the sale", "pause or rate changed"}


def test_the_measured_matrix_is_unchanged(console, comparison, measured_before):
    matrix = console.get("/api/matrix")
    assert matrix["read_only"] and len(matrix["tables"]["comparison"]) == 27
    assert digest(MEASURED) == measured_before
