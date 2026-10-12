"""Saved sessions: eligibility for comparisons, exports, replay data and the matrix."""

import json

import pytest
from fastapi.testclient import TestClient
from streamscale_console.app import Console, create_app, register_core_operations
from streamscale_console.operations import Journal
from streamscale_console.sales import Component as Sales
from streamscale_console.sales import SessionStore
from streamscale_console.sessions import Component as Sessions
from streamscale_console.sessions import eligibility, workload_signature

from .test_api import COOKIE, ORIGIN, PORT, TOKEN, FakeHub

RECONCILED = {"status": "passed", "latency_s": {"p95": 9.6}, "stored_facts": 9781}


def record(**overrides):
    return {
        "id": "session-1",
        "state": "drained",
        "outcome": "completed",
        "reconciliation_status": "passed",
        "config": {"scenario": "flash-sale", "seed": 202, "schedule": [[15, 52]], "faults": {}},
    } | overrides


def test_only_complete_unmodified_reconciled_sessions_are_eligible():
    assert eligibility(record()) == []
    assert "sale stopped" in eligibility(record(outcome="stopped"))
    assert "faults injected" in eligibility(record(faults=[{"kind": "kafka_outage"}]))
    assert "scaling changed during the sale" in eligibility(record(segments=[{}]))
    assert "pause or rate changed" in eligibility(record(controls_changed=True))
    assert "not reconciled" in eligibility(record(reconciliation_status=None))
    assert "session draining" in eligibility(record(state="draining"))


def test_workload_signature_ignores_mode_and_session():
    first = record()["config"] | {"mode": "fixed", "session": "a"}
    second = record()["config"] | {"mode": "lag-keda", "session": "b"}
    assert workload_signature(first) == workload_signature(second)
    assert workload_signature(first) != workload_signature(first | {"seed": 203})


@pytest.fixture
def client(tmp_path):
    console = Console(TOKEN, PORT, hub=FakeHub(), journal=Journal(tmp_path))
    register_core_operations(console)
    sales = Sales(console)
    sales.store = SessionStore(tmp_path / "sessions")
    console.components += [sales, Sessions(console)]
    stored = sales.store.create(
        record()["config"] | {"preset": "flash-sale", "mode": "fixed"},
        {"run_name": "sale-x", "run_id": "id-x", "group_id": "g", "attempts": 10},
        "session-20261012000000-aaaaaa",
    )
    stored |= {"state": "drained", "outcome": "completed", "reconciliation_status": "passed"}
    sales.store.save(stored)
    folder = sales.store.path(stored["id"])
    point = {
        "t": 100.0,
        "rates": {"arrival": 390.0, "drain": 140.0, "accepted_sql": 138.0},
        "lag": [10, 20, 30],
        "lag_total": 60,
        "desired": 1,
        "ready": 1,
        "members": 1,
        "e2e_p95": 2.5,
        "db_p95": 0.005,
        "facts": 100,
        "dlq_copies": 1,
        "restarts": 0,
        "mode": "fixed",
    }
    lines = [
        json.dumps({"point": point | {"t": 100.0 + i, "lag_total": 60 + i}, "sale": {}})
        for i in range(5)
    ]
    (folder / "telemetry.jsonl").write_text("\n".join(lines) + "\n")
    (folder / "reconciliation.json").write_text(json.dumps(RECONCILED))
    app = create_app(console, background=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as test_client:
        test_client.cookies.set(COOKIE, TOKEN)
        test_client.session_id = stored["id"]
        yield test_client


def test_replay_data_contains_every_recorded_frame(client):
    detail = client.get(f"/api/sessions/{client.session_id}").json()
    assert [frame["point"]["lag_total"] for frame in detail["frames"]] == [60, 61, 62, 63, 64]
    assert detail["eligibility"] == [] and detail["metrics"]["peak_lag"] == 64
    assert detail["reconciliation"]["latency_s"]["p95"] == 9.6


def test_points_export_is_one_csv_row_per_frame(client):
    response = client.get(f"/api/sessions/{client.session_id}/export/points.csv")
    rows = response.text.strip().splitlines()
    assert response.headers["content-disposition"].startswith("attachment")
    assert rows[0].startswith("t,arrival_per_s,served_per_s")
    assert len(rows) == 6 and rows[-1].split(",")[7] == "64"


@pytest.mark.parametrize("name", ["../session.json", "secrets.txt", "telemetry.jsonl"])
def test_exports_are_an_allowlist(client, name):
    assert client.get(f"/api/sessions/{client.session_id}/export/{name}").status_code == 404


def test_unknown_or_traversing_session_ids_are_refused(client):
    assert client.get("/api/sessions/session-..%2F..%2Fetc").status_code == 404
    assert client.get("/api/sessions/not-a-session").status_code == 404


def test_measured_matrix_is_served_read_only(client):
    matrix = client.get("/api/matrix").json()
    assert matrix["read_only"] is True
    assert matrix["tables"]["aggregate"]
    assert "primary-outcomes.svg" in matrix["figures"]
    assert client.get("/api/matrix/figure/primary-outcomes.svg").status_code == 200
    assert client.get("/api/matrix/figure/report.md").status_code == 404
    assert client.post("/api/matrix", headers={"Origin": ORIGIN}).status_code == 405


def test_comparisons_group_sessions_by_identical_workload(client):
    groups = client.get("/api/comparisons").json()["groups"]
    assert len(groups) == 1 and groups[0]["sessions"][0]["eligible"] is True
