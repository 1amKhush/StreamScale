"""Session lifecycle over sample sequences: drains, restarts, backfill and orphans."""

import asyncio
import json

import pytest
from streamscale_console.app import Console
from streamscale_console.operations import Journal
from streamscale_console.sales import Component, SessionStore
from streamscale_console.workflow import WorkflowLock

from .test_api import FakeHub


def sample(run_name, sale_state, remaining=(5, 5, 5), status="published", recovered=None):
    sale = {"state": sale_state, "run_name": run_name if sale_state != "idle" else None}
    if sale_state == "idle":
        sale = {"state": "idle", "recovered": recovered}
    if sale_state == "finished":
        sale["outcome"] = "completed"
    return {
        "sale": sale,
        "run": {"run_name": run_name, "status": status, "run_id": "id-" + run_name},
        "kafka": {"partitions": [{"run_remaining": value} for value in remaining]},
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    console = Console("t" * 43, 18000, hub=FakeHub(), journal=Journal(tmp_path))
    console.lock = WorkflowLock(tmp_path / "workflow.lock")
    console.coordinator.queue = asyncio.Queue()
    component = Component(console)
    component.store = SessionStore(tmp_path / "sessions")
    console.components.append(component)
    clock = {"now": 1000.0}
    monkeypatch.setattr("streamscale_console.sales.time.time", lambda: clock["now"])
    return console, component, clock


def started(component, run_name="sale-a"):
    record = component.store.create(
        {"preset": "steady"},
        {"run_name": run_name, "run_id": "id-" + run_name, "group_id": "g", "attempts": 10},
    )
    component.console.lock.acquire("sale")
    component.attach(record)
    return record


def feed(component, clock, *samples, step=1.0):
    for item in samples:
        clock["now"] += step
        component.observe(item, {"t": clock["now"]}, True)


def stored(component, record):
    return json.loads((component.store.path(record["id"]) / "session.json").read_text())


def reconciles(console):
    return [op for op in console.coordinator.operations.values() if op.kind == "session.reconcile"]


def test_a_finished_sale_drains_closes_and_requests_reconciliation(setup):
    console, component, clock = setup
    record = started(component)
    feed(component, clock, sample("sale-a", "publishing", status="publishing"))
    feed(component, clock, sample("sale-a", "finished"))
    assert stored(component, record)["state"] == "draining"
    feed(component, clock, *[sample("sale-a", "finished", (0, 0, 0))] * 7)
    final = stored(component, record)
    assert final["state"] == "drained" and final["outcome"] == "completed"
    assert component.record is None and not console.lock.held
    assert len(reconciles(console)) == 1


def test_an_agent_restart_mid_sale_is_finished_as_interrupted(setup):
    console, component, clock = setup
    record = started(component)
    feed(component, clock, sample("sale-a", "publishing", status="publishing"))
    recovered = {"run_id": "id-sale-a", "console": {"outcome": "interrupted", "attempted": 4}}
    feed(component, clock, sample("sale-a", "idle", recovered=recovered))
    assert stored(component, record)["outcome"] == "interrupted"
    feed(component, clock, *[sample("sale-a", "idle", (0, 0, 0), recovered=recovered)] * 7)
    assert stored(component, record)["state"] == "drained"


def test_a_restarted_server_finishes_draining_a_published_run(setup):
    console, component, clock = setup
    record = component.store.create(
        {}, {"run_name": "sale-a", "run_id": "id-sale-a", "group_id": "g", "attempts": 10}
    )
    record["state"] = "draining"
    component.store.save(record)
    # The agent itself restarted earlier: its sale section is idle.
    feed(component, clock, sample("sale-a", "idle", (3, 0, 0)))
    assert component.record["id"] == record["id"] and console.lock.held
    feed(component, clock, *[sample("sale-a", "idle", (0, 0, 0))] * 7)
    assert stored(component, record)["state"] == "drained"


def test_backfilled_history_never_reattaches_an_old_sale(setup):
    console, component, clock = setup
    old = component.store.create(
        {}, {"run_name": "sale-old", "run_id": "id-sale-old", "group_id": "g", "attempts": 10}
    )
    old["state"] = "drained"
    component.store.save(old)
    component.observe(sample("sale-old", "publishing", status="publishing"), {}, False)
    assert component.record is None and not console.lock.held


def test_a_draining_session_superseded_by_another_run_is_closed(setup):
    console, component, clock = setup
    record = started(component)
    feed(component, clock, sample("sale-a", "finished"))
    feed(component, clock, sample("sale-b", "publishing", status="publishing"))
    assert stored(component, record)["state"] == "superseded"
    assert not reconciles(console)


def test_a_new_sale_is_not_mistaken_for_superseding_while_publishing(setup):
    console, component, clock = setup
    record = started(component, "sale-new")
    # The run pointer still names the previous run for a moment after start.
    feed(component, clock, sample("sale-old", "publishing", status="published"))
    assert stored(component, record)["state"] == "publishing"


def test_sessions_left_open_by_an_earlier_server_are_marked_orphaned(setup):
    console, component, clock = setup
    left = component.store.create(
        {}, {"run_name": "sale-x", "run_id": "id-sale-x", "group_id": "g", "attempts": 10}
    )
    feed(component, clock, sample("sale-y", "idle", (0, 0, 0)))
    assert stored(component, left)["state"] == "orphaned"
