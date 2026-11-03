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
