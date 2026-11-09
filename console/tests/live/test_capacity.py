"""Exercise the single twelve-partition configuration through the real console API."""

import json

import pytest
from streamscale_console.cluster import ARTIFACTS

from .conftest import wait_idle
from .test_scaling import reconciled, set_mode

pytestmark = pytest.mark.live


def test_full_capacity_preserves_partition_ownership_and_correctness(console):
    wait_idle(console)
    set_mode(console, "manual", replicas=12)
    started = console.run(
        "sale.start",
        {
            "preset": "custom",
            "scenario": "steady",
            "seed": 121212,
            "schedule": [[30, 300]],
            "faults": {"malformed_pct": 2, "duplicate_pct": 5, "late_pct": 5},
        },
    )
    assert started["state"] == "succeeded", started
    session = started["result"]["session"]
    try:
        full = console.wait(
            lambda state: (
                state["points"][-1]["ready"] == 12
                and sorted(
                    tuple(m["partitions"]) for m in state["sample"]["kafka"]["group"]["members"]
                )
                == [(p,) for p in range(12)]
            ),
            timeout=180,
            describe="twelve ready consumers each owning one partition",
        )
        assert full["sample"]["scaling"]["ownership"]["consistent"]
        assert len(full["sample"]["kafka"]["partitions"]) == 12
        record = reconciled(console, session)
        assert record["reconciliation_status"] == "passed", record
        set_mode(console, "manual", replicas=1)
        single = console.wait(
            lambda state: (
                state["points"][-1]["ready"] == 1
                and len(state["sample"]["kafka"]["group"]["members"]) == 1
                and sorted(state["sample"]["kafka"]["group"]["members"][0]["partitions"])
                == list(range(12))
            ),
            timeout=180,
            describe="one consumer reassigned all twelve partitions",
        )
        evidence = {
            "status": "passed",
            "partitions": 12,
            "ready_peak": 12,
            "ready_after_downscale": single["points"][-1]["ready"],
            "session": session,
            "reconciliation": record["reconciliation"],
        }
        (ARTIFACTS / "capacity-check.json").write_text(json.dumps(evidence, indent=2))
    finally:
        wait_idle(console)
        set_mode(console, "fixed")
