"""M10 live acceptance: a real sale moves the floor, and its outcomes reconcile."""

import json

import pytest
from streamscale_console.cluster import ARTIFACTS, kubectl

from .conftest import wait_idle

pytestmark = pytest.mark.live


def agent_exec(*arguments):
    return kubectl("exec", "deployment/streamscale-console-agent", "--", *arguments, timeout=180)


def test_flash_sale_builds_backlog_and_reconciles(console):
    wait_idle(console)
    started = console.run("sale.start", {"preset": "flash-sale"}, timeout=120)
    assert started["state"] == "succeeded", started
    sale = started["result"]
    assert sale["attempts"] > 9000
    # During the rush, one consumer falls behind on every lane.
    busy = console.wait(
        lambda state: (
            state["points"][-1]["lag"]
            and min(state["points"][-1]["lag"]) > 200
            and state["points"][-1]["members"] == 1
        ),
        timeout=120,
        describe="backlog on every partition with one consumer",
    )
    point = busy["points"][-1]
    assert point["rates"]["arrival"] > point["rates"]["drain"]
    assert (
        any(signal["id"] == "backlog-growing" for signal in point["signals"])
        or point["lag_total"] > 600
    )
    members = busy["sample"]["kafka"]["group"]["members"]
    assert sorted(members[0]["partitions"]) == [0, 1, 2]
    facts_during = busy["points"][-1]["facts"]
    drained = console.wait(
        lambda state: (
            state["sales"]["session"] is None
            and state["sample"]["run"]["run_name"] == sale["run_name"]
        ),
        timeout=420,
        describe="the sale to drain",
    )
    final = drained["points"][-1]
    assert final["lag_total"] == 0
    assert final["facts"] > facts_during
    assert final["dlq_copies"] > 0
    # The unchanged pipeline reconciler accepts the console run: every acknowledged
    # valid ID stored once, latest state correct, every invalid record in the DLQ.
    result = json.loads(
        agent_exec(
            "/app/.venv/bin/streamscale",
            "inspect",
            "--run-dir",
            f"/runs/console/runs/{sale['run_name']}",
        ).stdout
    )
    assert result["status"] == "passed", result
    assert result["missing_valid_ids"] == [] and result["duplicate_stored_ids"] == 0
    assert result["state_regressions"] == 0 and result["missing_dlq_sources"] == 0
    assert final["facts"] == result["stored_facts"]
    session = ARTIFACTS / "sessions" / sale["session"]
    record = json.loads((session / "session.json").read_text())
    assert record["state"] == "drained" and record["outcome"] == "completed"
    assert sum(1 for _ in (session / "telemetry.jsonl").open()) > 60


def test_inspection_shows_sql_facts_and_dead_letters(console):
    facts = console.get("/api/inspect/facts.recent?limit=5")["facts"]
    assert len(facts) == 5 and all(row["latency_s"] >= 0 for row in facts)
    order = console.get(f"/api/inspect/order?order_id={facts[0]['order_id']}")
    assert order["facts"] and order["state"]["status"] in {"CREATED", "PAID", "CANCELLED"}
    event = console.get(f"/api/inspect/event?event_id={facts[0]['event_id']}")
    assert event["fact"]["event_id"] == facts[0]["event_id"]
    assert event["acknowledgements"][0]["partition"] == event["fact"]["partition"]
    dead = console.get("/api/inspect/dlq.recent")
    assert dead["recent"] and all(row["error_code"] for row in dead["recent"])


def test_a_new_sale_is_refused_while_another_is_draining(console):
    wait_idle(console)
    started = console.run("sale.start", {"preset": "steady"}, timeout=120)
    assert started["state"] == "succeeded"
    try:
        second = console.submit("sale.start", {"preset": "flash-sale"})
        assert second.get("status") == 409
    finally:
        stopped = console.run("sale.stop", {}, timeout=120)
        assert stopped.get("state") == "succeeded", stopped
        wait_idle(console)
