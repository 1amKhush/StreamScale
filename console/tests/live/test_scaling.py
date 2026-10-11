"""M12 live acceptance: manual rescue, both autoscalers and single-controller ownership."""

import json
import time

import pytest
from streamscale_console.cluster import ARTIFACTS

from .conftest import kube_json, wait_idle

pytestmark = pytest.mark.live


def scaling(state):
    return state["sample"]["scaling"]


def set_mode(console, mode, **extra):
    result = console.run("scaling.mode", {"mode": mode, **extra}, timeout=240)
    assert result["state"] == "succeeded", result
    return result


def owners(state):
    return set(scaling(state)["ownership"]["found"])


def reconciled(console, session_id, timeout=600):
    console.wait(lambda state: state["sales"]["session"] is None, timeout, describe="drain")
    path = ARTIFACTS / "sessions" / session_id / "session.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        record = json.loads(path.read_text())
        if record.get("reconciliation_status"):
            return record
        time.sleep(2)
    raise AssertionError("Reconciliation did not finish")


@pytest.fixture(autouse=True)
def restore_fixed(console):
    wait_idle(console)
    yield
    wait_idle(console)
    set_mode(console, "fixed")


def test_manual_rescue_spreads_lanes_and_speeds_the_drain(console):
    set_mode(console, "fixed")
    started = console.run("sale.start", {"preset": "flash-sale-extended"})
    session = started["result"]["session"]
    behind = console.wait(
        lambda state: (
            (state["points"][-1]["lag_total"] or 0) > 3000 and state["points"][-1]["members"] == 1
        ),
        timeout=120,
        describe="one consumer falling behind",
    )
    slow = [point["rates"]["drain"] for point in behind["points"][-5:] if point["rates"]["drain"]]
    one_consumer = sum(slow) / len(slow)
    set_mode(console, "manual", replicas=3)
    rescued = console.wait(
        lambda state: (
            state["points"][-1]["members"] == 3
            and sorted(
                tuple(member["partitions"])
                for member in state["sample"]["kafka"]["group"]["members"]
            )
            == [(0,), (1,), (2,)]
        ),
        timeout=120,
        describe="three consumers owning one lane each",
    )
    assert owners(rescued) == set()  # manual mode: no autoscaler owns the Deployment
    time.sleep(10)
    fast = [
        point["rates"]["drain"]
        for point in console.state()["points"][-6:]
        if point["rates"]["drain"]
    ]
    assert sum(fast) / len(fast) > 1.8 * one_consumer
    assert console.run("sale.stop")["state"] == "succeeded"
    record = reconciled(console, session)
    assert record["reconciliation_status"] == "passed", record["reconciliation"]
    assert record["segments"] and record["comparable"] is False


@pytest.mark.parametrize(
    ("mode", "expected"), [("cpu-hpa", {"hpa"}), ("lag-keda", {"keda", "keda-hpa"})]
)
def test_autoscaler_adds_consumers_under_a_flash_sale(console, mode, expected):
    set_mode(console, mode)
    state = console.wait(
        lambda state: scaling(state)["ownership"]["consistent"], 60, describe="ownership"
    )
    started = console.run("sale.start", {"preset": "flash-sale"})
    session = started["result"]["session"]
    if mode == "lag-keda":
        scaled = kube_json("get", "scaledobject", "streamscale-console-consumer")
        trigger = scaled["spec"]["triggers"][0]["metadata"]
        assert trigger["consumerGroup"] == started["result"]["group_id"]
    peak = console.wait(
        lambda state: (
            (state["points"][-1]["desired"] or 0) >= 2 and state["points"][-1]["ready"] >= 2
        ),
        timeout=180,
        describe=f"{mode} to add a ready consumer",
    )
    assert owners(peak) == expected
    hpas = kube_json("get", "hpa")["items"]
    targeting = [
        row
        for row in hpas
        if row["spec"]["scaleTargetRef"]["name"] == "streamscale-console-consumer"
    ]
    assert len(targeting) == 1  # never two HPAs on the same Deployment
    record = reconciled(console, session)
    assert record["reconciliation_status"] == "passed", record["reconciliation"]
    assert state is not None


def test_override_suspends_the_autoscaler_and_release_restores_it(console):
    set_mode(console, "lag-keda")
    started = console.run("sale.start", {"preset": "flash-sale-extended"})
    session = started["result"]["session"]
    console.wait(
        lambda state: (state["points"][-1]["lag_total"] or 0) > 1000, 120, describe="backlog"
    )
    result = console.run("scaling.override", {"replicas": 1}, timeout=240)
    assert result["state"] == "succeeded", result
    held = console.wait(
        lambda state: owners(state) == set() and state["points"][-1]["desired"] == 1,
        timeout=60,
        describe="the ScaledObject to be removed",
    )
    assert scaling(held)["override"]["replicas"] == 1
    time.sleep(15)
    assert console.state()["points"][-1]["desired"] == 1  # nobody overrode the override
    assert console.run("scaling.release", timeout=240)["state"] == "succeeded"
    console.wait(
        lambda state: (
            owners(state) == {"keda", "keda-hpa"}
            and not scaling(state)["override"]
            and (state["points"][-1]["desired"] or 0) >= 2
        ),
        timeout=120,
        describe="KEDA to resume ownership and scale up",
    )
    assert console.run("sale.stop")["state"] == "succeeded"
    record = reconciled(console, session)
    assert record["reconciliation_status"] == "passed", record["reconciliation"]


def test_zero_manual_replicas_is_stopped_processing_not_a_crash(console):
    set_mode(console, "manual", replicas=1)
    started = console.run("sale.start", {"preset": "steady"})
    session = started["result"]["session"]
    console.wait(lambda state: state["sample"]["sale"]["acknowledged"] > 200, 60, describe="sales")
    restarts = console.state()["points"][-1]["restarts"]
    assert console.run("scaling.replicas", {"replicas": 0}, timeout=240)["state"] == "succeeded"
    stopped = console.wait(
        lambda state: (
            state["points"][-1]["members"] == 0
            and any(signal["id"] == "no-consumers" for signal in state["points"][-1]["signals"])
        ),
        timeout=90,
        describe="processing to stop",
    )
    assert stopped["points"][-1]["restarts"] <= restarts  # stopped, not crashed
    assert console.run("scaling.replicas", {"replicas": 1}, timeout=240)["state"] == "succeeded"
    assert console.run("sale.stop")["state"] == "succeeded"
    record = reconciled(console, session)
    assert record["reconciliation_status"] == "passed", record["reconciliation"]
