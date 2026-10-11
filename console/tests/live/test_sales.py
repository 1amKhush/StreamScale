"""M11 live acceptance: interactive sale controls, partial runs and restart recovery."""

import json
import time
import uuid

import pytest
from streamscale_console.cluster import ARTIFACTS, kubectl

from .conftest import wait_idle

pytestmark = pytest.mark.live

# Ten minutes at 52/s, so live rate changes cannot exhaust the budget mid-test.
LONG = {
    "preset": "custom",
    "scenario": "steady",
    "seed": 101,
    "schedule": [[600, 52]],
    "faults": {"malformed_pct": 1, "duplicate_pct": 2, "late_pct": 5},
}


def sale(state):
    return state["sample"]["sale"]


def acknowledged(state, run_name):
    """This sale's acknowledgements; an earlier sale's last sample must not count."""
    current = state["sample"].get("sale") or {}
    return current.get("acknowledged", 0) if current.get("run_name") == run_name else 0


def high_total(state):
    return sum(row["high"] for row in state["sample"]["kafka"]["partitions"])


def finished_session(console, session_id, timeout=600):
    """Wait for drain and reconciliation, then return the stored session record."""
    console.wait(
        lambda state: state["sales"]["session"] is None,
        timeout=timeout,
        describe="the sale to drain",
    )
    path = ARTIFACTS / "sessions" / session_id / "session.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        record = json.loads(path.read_text())
        if record.get("reconciliation_status"):
            return record
        time.sleep(2)
    raise AssertionError("Reconciliation did not finish")


def test_holding_the_doors_stops_production_while_cashiers_keep_serving(console):
    wait_idle(console)
    started = console.run("sale.start", LONG)
    assert started["state"] == "succeeded", started
    session = started["result"]["session"]
    assert console.run("sale.rate", {"rate": 400})["state"] == "succeeded"
    console.wait(
        lambda state: (state["points"][-1]["rates"]["arrival"] or 0) > 300,
        timeout=30,
        describe="the live rate to take effect",
    )
    console.wait(
        lambda state: (state["points"][-1]["lag_total"] or 0) > 1500,
        timeout=90,
        describe="a backlog to build",
    )
    assert console.run("sale.pause")["state"] == "succeeded"
    console.wait(lambda state: sale(state)["state"] == "paused", 30, describe="pause")
    time.sleep(3)  # in-flight deliveries settle; no new records are produced
    before = console.state()
    time.sleep(6)
    after = console.state()
    assert sale(after)["attempted"] == sale(before)["attempted"]
    assert sale(after)["acknowledged"] == sale(before)["acknowledged"]
    assert high_total(after) == high_total(before)
    # Consumers keep working through the queue during the hold.
    assert after["points"][-1]["facts"] > before["points"][-1]["facts"]
    assert after["points"][-1]["lag_total"] < before["points"][-1]["lag_total"]
    assert console.run("sale.resume")["state"] == "succeeded"
    console.wait(
        lambda state: sale(state)["acknowledged"] > sale(after)["acknowledged"],
        timeout=30,
        describe="publishing to resume",
    )
    assert console.run("sale.rate", {"rate": None})["state"] == "succeeded"
    console.wait(
        lambda state: (
            sale(state)["rate_mode"] == "schedule"
            and 0 < (state["points"][-1]["rates"]["arrival"] or 0) < 120
        ),
        timeout=30,
        describe="the schedule's 52/s to resume",
    )
    assert console.run("sale.stop")["state"] == "succeeded"
    record = finished_session(console, session)
    summary = record["sale_summary"]
    assert record["outcome"] == "stopped"
    assert summary["attempted"] < summary["budget"]
    # Stopping flushes: everything sent was acknowledged, nothing failed.
    assert summary["acknowledged"] == summary["attempted"] and summary["failed"] == 0
    assert record["reconciliation_status"] == "passed", record["reconciliation"]
    assert record["reconciliation"]["missing_acknowledged_count"] == 0
    assert record["comparable"] is False


def test_abort_keeps_a_reconciled_partial_result(console):
    wait_idle(console)
    started = console.run("sale.start", {"preset": "flash-sale"})
    session, run = started["result"]["session"], started["result"]["run_name"]
    console.wait(lambda state: acknowledged(state, run) > 500, 60, describe="sales")
    assert console.run("sale.abort")["state"] == "succeeded"
    record = finished_session(console, session)
    assert record["outcome"] == "aborted"
    assert record["sale_summary"]["attempted"] < record["sale_summary"]["budget"]
    assert record["reconciliation_status"] == "passed", record["reconciliation"]
    assert record["comparable"] is False


def test_agent_restart_mid_sale_finalizes_the_acknowledged_subset(console):
    wait_idle(console)
    started = console.run("sale.start", LONG)
    session, run = started["result"]["session"], started["result"]["run_name"]
    console.run("sale.rate", {"rate": 300})
    console.wait(lambda state: acknowledged(state, run) > 1500, 60, describe="sales")
    kubectl("delete", "pod", "-l", "app=streamscale-console-agent", "--wait=false")
    record = finished_session(console, session)
    assert record["outcome"] == "interrupted"
    result = json.loads((ARTIFACTS / "sessions" / session / "reconciliation.json").read_text())
    assert result["status"] == "passed", result["checks"]
    assert result["missing_acknowledged_count"] == 0
    assert result["acknowledged"] > 1500
    assert result["acknowledged"] + result["unacknowledged_in_log"] == result["log_records"]


def test_a_restarted_console_server_reattaches_to_the_running_sale(console_server):
    console, server = console_server[0], console_server[2]
    wait_idle(console)
    started = console.run("sale.start", {"preset": "steady"})
    run_name = started["result"]["run_name"]
    session = started["result"]["session"]
    console.wait(lambda state: acknowledged(state, run_name) > 200, 30, describe="sales")
    console = server.restart()
    state = console.wait(
        lambda state: (state["sales"]["session"] or {}).get("run_name") == run_name,
        timeout=60,
        describe="the restarted server to reattach",
    )
    assert state["workflow"]["held_by_console"]
    assert any("Reattached" in (entry.get("text") or "") for entry in state["journal"])
    assert console.run("sale.stop")["state"] == "succeeded"
    record = finished_session(console, session)
    assert record["reconciliation_status"] == "passed"


def test_one_intent_publishes_one_sale(console):
    wait_idle(console)
    key = "test-" + uuid.uuid4().hex
    first = console.submit("sale.start", {"preset": "steady"}, key)
    again = console.submit("sale.start", {"preset": "steady"}, key)
    assert first["operation"]["id"] == again["operation"]["id"] and again["created"] is False
    console.wait(
        lambda state: (state["sales"]["session"] or {}).get("run_name"),
        timeout=60,
        describe="the sale to start",
    )
    other = console.submit("sale.start", {"preset": "flash-sale"})
    assert other["status"] == 409
    assert console.run("sale.stop")["state"] == "succeeded"
    wait_idle(console)
    starts = [
        entry
        for entry in console.state()["journal"]
        if entry.get("op_id") == first["operation"]["id"] and entry.get("state") == "succeeded"
    ]
    assert len(starts) == 1
