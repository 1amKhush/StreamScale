"""Real predictive pilot and manual ownership; requires predictive-prepare."""

import json

import pytest
from streamscale_console.cluster import ARTIFACTS, ROOT

from .conftest import wait_idle
from .test_scaling import reconciled, set_mode

pytestmark = pytest.mark.live


def test_predictive_pilot_and_override_reconcile(console):
    pointer = ROOT / "artifacts/predictive/current.json"
    assert pointer.exists(), "Run ./streamscale.sh predictive-prepare first"
    wait_idle(console)
    try:
        set_mode(console, "ml-predictive")
        started = console.run("sale.start", {"preset": "predictive-pilot"})
        assert started["state"] == "succeeded", started
        session = started["result"]["session"]
        state = console.wait(
            lambda s: (
                (s["sample"]["scaling"].get("predictive") or {}).get("forecast_rate") is not None
            ),
            150,
            describe="history-only forecast",
        )
        assert state["sample"]["scaling"]["ownership"]["consistent"]
        override = console.run("scaling.override", {"replicas": 2})
        assert override["state"] == "succeeded", override
        assert console.run("scaling.release")["state"] == "succeeded"
        assert (
            console.run("fault.inject", {"kind": "crash_after_db_commit", "params": {}})["state"]
            == "succeeded"
        )
        assert (
            console.run("fault.inject", {"kind": "postgres_outage", "params": {"duration_s": 10}})[
                "state"
            ]
            == "succeeded"
        )
        console.wait(
            lambda s: not (s["sample"].get("faults") or {}).get("active"),
            120,
            describe="automatic fault recovery",
        )
        record = reconciled(console, session)
        assert record["reconciliation_status"] == "passed"
        assert record["config"]["mode"] == "ml-predictive"
        (ARTIFACTS / "predictive-check.json").write_text(
            json.dumps(
                {"status": "passed", "session": session, "reconciliation": record["reconciliation"]}
            )
        )
    finally:
        wait_idle(console)
        set_mode(console, "fixed")


def test_predictive_round_trips_every_controller_owner(console):
    """Exercise direct HPA/KEDA/manual/fixed transitions, including a full tick after exit."""
    import time

    wait_idle(console)
    evidence = []
    try:
        for mode in ("fixed", "manual", "cpu-hpa", "lag-keda"):
            extra = {"replicas": 2} if mode == "manual" else {}
            set_mode(console, mode, **extra)
            console.wait(
                lambda s, mode=mode: (
                    s["sample"]["scaling"]["mode"] == mode
                    and s["sample"]["scaling"]["ownership"]["consistent"]
                ),
                90,
            )
            set_mode(console, "ml-predictive")
            entered = console.wait(
                lambda s: (
                    s["sample"]["scaling"]["mode"] == "ml-predictive"
                    and s["sample"]["scaling"]["ownership"]["consistent"]
                    and not s["sample"]["scaling"]["ownership"]["found"]
                ),
                90,
                describe=f"{mode} to predictive ownership",
            )
            set_mode(console, mode, **extra)
            console.wait(
                lambda s, mode=mode: (
                    s["sample"]["scaling"]["mode"] == mode
                    and s["sample"]["scaling"]["ownership"]["consistent"]
                ),
                90,
            )
            time.sleep(6)
            state = console.state()["sample"]["scaling"]
            assert state["mode"] == mode and state["ownership"]["consistent"]
            assert state["predictive"]["status"] == "inactive"
            if mode in {"fixed", "manual"}:
                assert console.state()["points"][-1]["desired"] == (2 if mode == "manual" else 1)
            evidence.append(
                {
                    "mode": mode,
                    "entered": entered["sample"]["scaling"]["ownership"],
                    "returned": state["ownership"],
                }
            )
        (ARTIFACTS / "predictive-owner-transitions.json").write_text(
            json.dumps({"status": "passed", "round_trips": evidence}, indent=2) + "\n"
        )
    finally:
        wait_idle(console)
        set_mode(console, "fixed")


def test_predictive_agent_restart_rebuilds_history_and_retains_override(console):
    """Restart only the scoped agent; leave every publisher/consumer/data service alone."""
    import time

    from streamscale_console.cluster import kubectl

    from .conftest import kube_json

    def agent_uid():
        pods = kube_json("get", "pods")["items"]
        return next(
            p["metadata"]["uid"]
            for p in pods
            if p["metadata"]["name"].startswith("streamscale-console-agent-")
            and not p["metadata"].get("deletionTimestamp")
        )

    def restart():
        previous = agent_uid()
        requested_at = time.time()
        kubectl("rollout", "restart", "deployment/streamscale-console-agent")
        kubectl("rollout", "status", "deployment/streamscale-console-agent", "--timeout=120s")
        assert agent_uid() != previous
        return {"before_uid": previous, "after_uid": agent_uid(), "requested_at": requested_at}

    wait_idle(console)
    try:
        set_mode(console, "ml-predictive")
        before = console.wait(
            lambda s: (
                (s["sample"]["scaling"].get("predictive") or {}).get("forecast_rate") is not None
            ),
            150,
            describe="a forecast before agent restart",
        )
        first = restart()
        cold = console.wait(
            lambda s: (
                s["status"]["agent"] == "connected"
                and s["points"][-1]["t"] >= first["requested_at"]
                and s["sample"]["scaling"]["mode"] == "ml-predictive"
                and s["sample"]["scaling"]["ownership"]["consistent"]
                and (s["sample"]["scaling"].get("predictive") or {}).get("status") == "warming up"
                and (s["sample"]["scaling"].get("predictive") or {}).get("forecast_rate") is None
                and (s["sample"]["scaling"].get("predictive") or {}).get("model_id")
                == (before["sample"]["scaling"]["predictive"]["model_id"])
            ),
            45,
            describe="fresh predictive history after restart",
        )
        assert (
            cold["sample"]["scaling"]["predictive"]["model_id"]
            == (before["sample"]["scaling"]["predictive"]["model_id"])
        )
        assert console.run("scaling.override", {"replicas": 2})["state"] == "succeeded"
        second = restart()
        held = console.wait(
            lambda s: (
                s["status"]["agent"] == "connected"
                and s["points"][-1]["t"] >= second["requested_at"]
                and bool(s["sample"]["scaling"].get("override"))
                and s["sample"]["scaling"]["override"]["replicas"] == 2
                and s["points"][-1]["desired"] == 2
                and s["sample"]["scaling"]["predictive"]["status"] == "manual override"
                and s["sample"]["scaling"]["ownership"]["consistent"]
            ),
            90,
            describe="persisted override after agent restart",
        )
        assert not held["sample"]["scaling"]["ownership"]["found"]
        assert console.run("scaling.release")["state"] == "succeeded"
        console.wait(
            lambda s: (
                not s["sample"]["scaling"]["override"]
                and s["sample"]["scaling"]["predictive"]["status"] == "warming up"
            ),
            45,
            describe="fresh history after override release",
        )
        (ARTIFACTS / "predictive-agent-restart.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "agent_uid_changes": [first, second],
                    "model_id": before["sample"]["scaling"]["predictive"]["model_id"],
                    "history_rebuilt": True,
                    "manual_override_retained": True,
                    "scope": "Idle agent restarts; no active sale, no data-service restart.",
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        wait_idle(console)
        set_mode(console, "fixed")
