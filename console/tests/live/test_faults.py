"""M13 live acceptance: real failures, observed reasons, recovery and reconciliation."""

import json
import time

import pytest
from streamscale_console.cluster import ARTIFACTS

from .conftest import kube_json, wait_idle

pytestmark = pytest.mark.live

UNRELATED = ("app=streamscale-grafana", "app=streamscale-prometheus", "app=streamscale-exporter")


def restarts(selector):
    pods = kube_json("get", "pods", "-l", selector)["items"]
    return {
        pod["metadata"]["name"]: sum(
            c.get("restartCount", 0) for c in pod["status"].get("containerStatuses", [])
        )
        for pod in pods
    }


@pytest.fixture(scope="module")
def unrelated_before():
    return {selector: restarts(selector) for selector in UNRELATED}


@pytest.fixture(autouse=True)
def clean_floor(console):
    wait_idle(console)
    if console.state()["sample"]["scaling"]["mode"] != "fixed":
        assert console.run("scaling.mode", {"mode": "fixed"}, timeout=240)["state"] == "succeeded"
    yield
    console.run("fault.recover_all", timeout=120)
    wait_idle(console)


def faults(state):
    return state["sample"]["faults"]


def inject(console, kind, **params):
    """Inject and return the new fault's ID; outcomes are tracked by ID, never by kind."""
    result = console.run("fault.inject", {"kind": kind, "params": params})
    assert result["state"] == "succeeded", result
    return result["result"]["id"]


def outcome(console, fault_id, timeout=240):
    """Wait until this fault (not an older one of the same kind) has a verified outcome."""

    def find(state):
        rows = faults(state)["history"] + faults(state)["active"]
        return next((fault for fault in rows if fault["id"] == fault_id), None)

    state = console.wait(
        lambda state: (find(state) or {}).get("verified") is not None,
        timeout=timeout,
        describe=f"the outcome of {fault_id}",
    )
    return find(state)


LONG = {
    "preset": "custom",
    "scenario": "steady",
    "seed": 101,
    "schedule": [[600, 52]],
    "faults": {"malformed_pct": 1, "duplicate_pct": 2, "late_pct": 5},
}


def start(console, rate=150):
    # A ten-minute budget, so no fault outlives the sale it is observed in.
    started = console.run("sale.start", LONG)
    assert started["state"] == "succeeded", started
    if rate:
        console.run("sale.rate", {"rate": rate})
    console.wait(lambda state: state["sample"]["sale"]["acknowledged"] > 300, 60, describe="sales")
    return started["result"]


def finish(console, sale):
    if (console.state()["sample"].get("sale") or {}).get("state") in {"publishing", "paused"}:
        console.run("sale.stop")
    console.wait(lambda state: state["sales"]["session"] is None, 600, describe="drain")
    path = ARTIFACTS / "sessions" / sale["session"] / "session.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        record = json.loads(path.read_text())
        if record.get("reconciliation_status"):
            assert record["reconciliation_status"] == "passed", record["reconciliation"]
            assert record["comparable"] is False and record["faults"]
            return record
        time.sleep(2)
    raise AssertionError("Reconciliation did not finish")


def test_crash_after_db_commit_replays_and_stores_once(console):
    sale = start(console)
    before = console.state()["points"][-1]["restarts"]
    fault = outcome(console, inject(console, "crash_after_db_commit"))
    assert fault["verified"] is True, fault["events"]
    assert fault["claim"]["exit_code"] == 75
    state = console.wait(lambda s: s["points"][-1]["restarts"] > before, 60, describe="restart")
    pod = next(
        p for p in state["sample"]["kubernetes"]["pods"] if p["name"] == fault["claim"]["pod"]
    )
    assert pod["last_termination"]["exit_code"] == 75
    event = console.get(f"/api/inspect/event?event_id={fault['claim']['event_id']}")
    assert event["fact"]["offset"] == fault["claim"]["offset"]
    finish(console, sale)


def test_crash_after_dlq_ack_is_counted_by_unique_source(console):
    sale = start(console, rate=200)
    fault = outcome(console, inject(console, "crash_after_dlq_ack"))
    assert fault["claim"]["exit_code"] == 76 and fault["verified"] is True
    record = finish(console, sale)
    assert record["reconciliation"]["missing_dlq_sources"] == 0
    assert record["reconciliation"]["dlq_copies"] >= record["reconciliation"]["dlq_unique_sources"]


def test_postgres_outage_holds_offsets_then_drains(console):
    sale = start(console, rate=100)
    assert (
        console.run("fault.inject", {"kind": "postgres_outage", "params": {"duration_s": 20}})[
            "state"
        ]
        == "succeeded"
    )
    down = console.wait(
        lambda s: any(sig["id"] == "postgres-unavailable" for sig in s["points"][-1]["signals"]),
        60,
        describe="the outage to be visible",
    )
    time.sleep(6)
    held = console.state()
    committed = [row["committed"] for row in held["sample"]["kafka"]["partitions"]]
    time.sleep(6)
    still = console.state()
    # No offset advances without a durable write; records keep arriving.
    assert [row["committed"] for row in still["sample"]["kafka"]["partitions"]] == committed
    assert still["sample"]["sale"]["acknowledged"] > held["sample"]["sale"]["acknowledged"]
    console.wait(
        lambda s: not faults(s)["active"] and s["points"][-1]["rates"]["drain"],
        120,
        describe="automatic recovery and draining",
    )
    finish(console, sale)
    assert down is not None


def test_kafka_outage_buffers_without_losing_acknowledged_events(console):
    sale = start(console, rate=100)
    assert (
        console.run("fault.inject", {"kind": "kafka_outage", "params": {"duration_s": 20}})["state"]
        == "succeeded"
    )
    console.wait(lambda s: not faults(s)["active"], 180, describe="the broker to return")
    console.wait(
        lambda s: (
            s["sample"]["sale"]["acknowledged"] > 0
            and (s["points"][-1]["rates"]["acknowledged"] or 0) > 50
        ),
        180,
        describe="acknowledgements to resume",
    )
    record = finish(console, sale)
    assert record["sale_summary"]["failed"] == 0


def test_memory_exhaustion_kills_one_bounded_pod(console):
    sale = start(console, rate=100)
    fault = outcome(console, inject(console, "memory_pressure"), timeout=300)
    assert fault["verified"] is True
    assert any("OOMKilled" in event["text"] for event in fault["events"])
    finish(console, sale)


def test_cpu_restriction_is_applied_in_place_and_restored(console):
    sale = start(console, rate=100)
    restarts_before = console.state()["points"][-1]["restarts"]
    assert (
        console.run(
            "fault.inject", {"kind": "restrict_cpu", "params": {"duration_s": 20, "limit_m": 100}}
        )["state"]
        == "succeeded"
    )
    console.wait(
        lambda s: all(p["cpu_limit"] == "100m" for p in s["sample"]["kubernetes"]["pods"]),
        60,
        describe="the in-place CPU limit",
    )
    assert console.state()["points"][-1]["restarts"] == restarts_before  # no restart
    console.wait(
        lambda s: (
            not faults(s)["active"]
            and all(p["cpu_limit"] == "500m" for p in s["sample"]["kubernetes"]["pods"])
        ),
        90,
        describe="the original limit",
    )
    finish(console, sale)


def test_recover_all_restores_every_fault(console):
    sale = start(console, rate=100)
    for kind, params in (
        ("slow_processing", {"duration_s": 120, "delay_ms": 30}),
        ("restrict_db_cpu", {"duration_s": 120, "limit_m": 50}),
        ("stop_consumers", {"duration_s": 120}),
    ):
        assert console.run("fault.inject", {"kind": kind, "params": params})["state"] == "succeeded"
    console.wait(lambda s: s["points"][-1]["members"] == 0, 90, describe="consumers to stop")
    assert console.run("fault.recover_all", timeout=240)["state"] == "succeeded"
    state = console.wait(
        lambda s: (
            not faults(s)["active"]
            and s["sample"]["scaling"]["mode"] == "fixed"
            and s["points"][-1]["members"] == 1
            and s["sample"]["kubernetes"]["infrastructure"]["postgres"]["pod"]["cpu_limit"]
            == "500m"
        ),
        180,
        describe="every fault to be recovered",
    )
    assert not state["sample"]["scaling"]["override"]
    finish(console, sale)


def test_unrelated_services_were_not_restarted(unrelated_before):
    for selector, before in unrelated_before.items():
        after = restarts(selector)
        for name, count in before.items():
            assert after.get(name, count) == count, f"{name} restarted during the failure lab"
