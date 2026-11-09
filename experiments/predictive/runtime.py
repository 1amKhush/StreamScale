"""Headless use of the existing console coordinator and reconciliation lifecycle."""

import asyncio
import base64
import json
import secrets
import time
from contextlib import asynccontextmanager

from streamscale_console.app import Console, create_app, register_core_operations
from streamscale_console.cluster import ARTIFACTS, kubectl
from streamscale_console.components import attach
from streamscale_console.sales import comparable
from streamscale_console.workflow import WorkflowLock

from streamscale.predictive.data import observation


class ReservedLock(WorkflowLock):
    def release(self):
        pass  # reserve between sales as well; only the outer context releases it

    def close(self):
        super().release()


def complete_workload(record):
    """Correct SQL for a partial sale is not a completed experimental workload."""
    result = record.get("reconciliation") or {}
    attempts = record.get("attempts")
    return (
        record.get("reconciliation_status") == "passed"
        and result.get("status") == "passed"
        and record.get("outcome") == "completed"
        and record.get("comparable") is True
        and type(attempts) is int
        and attempts > 0
        and result.get("acknowledged") == attempts
        and comparable(record, result)
    )


@asynccontextmanager
async def driver():
    from streamscale_console.__main__ import running_server

    if running_server(ARTIFACTS / "server.json"):
        raise RuntimeError("Close the console server with Ctrl+C before a predictive campaign")
    console = Console(secrets.token_urlsafe(32), 0)
    console.lock = ReservedLock()
    console.lock.acquire("predictive campaign")
    register_core_operations(console)
    attach(console)
    app = create_app(console)
    try:
        async with app.router.lifespan_context(app):
            deadline = time.monotonic() + 90
            while console.hub.status["agent"] != "connected":
                if time.monotonic() > deadline:
                    raise RuntimeError("Agent unavailable; deploy the console first")
                await asyncio.sleep(1)
            sales = next(c for c in console.components if hasattr(c, "store"))
            if sales.record or (console.hub.latest.get("sale") or {}).get("state") in {
                "preparing",
                "publishing",
                "paused",
                "stopping",
            }:
                raise RuntimeError("An earlier sale is active; wait for it to drain")
            if (console.hub.latest.get("faults") or {}).get("active"):
                raise RuntimeError("Recover active faults before predictive experiments")
            yield console
    finally:
        console.lock.close()


async def operation(console, kind, params=None):
    op, _ = console.coordinator.submit(kind, params or {}, secrets.token_hex(16))
    deadline = time.monotonic() + 300
    while op.state in {"queued", "running"}:
        if time.monotonic() > deadline:
            raise RuntimeError(f"Operation timed out: {kind}")
        await asyncio.sleep(0.2)
    if op.state != "succeeded":
        raise RuntimeError(f"{kind}: {op.error}")
    return op.result


async def capture(console, config, mode, replicas=None):
    actuation_started_at = time.time()
    settings = {"mode": mode}
    if replicas is not None:
        settings["replicas"] = replicas
    await operation(console, "scaling.mode", settings)
    # A consistent initial state for every trial; do not carry prior replica count.
    if mode != "manual":
        if mode != "fixed":
            await operation(console, "scaling.override", {"replicas": 1})
            await operation(console, "scaling.release")
    await asyncio.sleep(5)
    started = await operation(console, "sale.start", config)
    sales = next(c for c in console.components if hasattr(c, "store"))
    identifier = started["session"]
    observations, points = [], []
    sequence = None
    deadline = time.monotonic() + sum(p[0] for p in config["schedule"]) + 600
    while time.monotonic() < deadline:
        sample = console.hub.latest or {}
        if (
            sample.get("sequence") != sequence
            and (sample.get("run") or {}).get("run_id") == started["run_id"]
        ):
            sequence = sample.get("sequence")
            try:
                observations.append(observation(sample))
            except (ValueError, KeyError, TypeError):
                pass
            if console.hub.points:
                points.append(console.hub.points[-1])
        record = sales.store.load(identifier)
        if record.get("reconciliation_status"):
            if record["reconciliation_status"] != "passed":
                raise RuntimeError(f"Correctness failed for {identifier}")
            if not complete_workload(record):
                raise RuntimeError(
                    f"Incomplete or modified workload {identifier}; "
                    "retained outside experimental results"
                )
            return {
                "actuation_started_at": actuation_started_at,
                "id": identifier,
                "family": config.get("family", config["scenario"]),
                "observations": observations,
                "points": points,
                "record": record,
                "config": config,
                "mode": mode,
            }
        await asyncio.sleep(1)
    raise RuntimeError(f"Sale did not reconcile: {identifier}; retained for inspection")


def install(folder):
    """Copy trusted local model artifacts to the existing scoped runs PVC."""
    identifier = json.loads((folder / "current.json").read_text())["id"]
    files = {
        name: base64.b64encode((folder / name).read_bytes()).decode()
        for name in (f"{identifier}.pkl", f"{identifier}.json", "current.json")
    }
    script = (
        "import sys,json,base64,os; from pathlib import Path; "
        "p=Path('/runs/console/predictive'); p.mkdir(parents=True,exist_ok=True); "
        "files=json.load(sys.stdin); "
        "[(p.joinpath(k+'.tmp').write_bytes(base64.b64decode(v)), "
        "os.replace(p/(k+'.tmp'),p/k)) for k,v in files.items()]"
    )
    kubectl(
        "exec",
        "-i",
        "deployment/streamscale-console-agent",
        "--",
        "/app/.venv/bin/python",
        "-c",
        script,
        input=json.dumps(files),
    )
