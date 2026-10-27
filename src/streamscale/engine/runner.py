"""Paced execution, fault lifecycle, bounded drain and durable per-run evidence."""

import copy
import hashlib
import inspect
import json
import math
import random
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from streamscale.engine.checker import reconcile
from streamscale.engine.model import Context, symbol, validate

EXIT = {"pass": 0, "fail": 1, "error": 2, "inconclusive": 3}


def now():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def logical_plan(config):
    rng = random.Random(config.get("seed", 42))
    records = []
    start = 0.0
    for phase in config["phases"]:
        count = math.ceil(phase["seconds"] * phase["rate"])
        for i in range(count):
            at = i / phase["rate"]
            if at < phase["seconds"]:
                records.append(
                    {
                        "index": len(records),
                        "at_seconds": start + at,
                        "payload": copy.deepcopy(rng.choice(config["payloads"])),
                    }
                )
        start += phase["seconds"]
    return records


def hook(config, name, context):
    spec = config.get("hooks", {}).get(name)
    if not spec:
        return
    import os

    environment = os.environ | {
        "STREAMSCALE_RUN_ID": context.run_id,
        "STREAMSCALE_RUN_DIR": context.directory,
    }
    # No shell interpolation and no command output/credentials in evidence.
    subprocess.run(
        spec["argv"],
        env=environment,
        check=True,
        timeout=spec.get("timeout_seconds", 10),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def provenance():
    def git(*args):
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None

    return {
        "engine_git_revision": git("rev-parse", "HEAD"),
        "engine_git_dirty": bool(git("status", "--porcelain")),
        "engine_source_sha256": digest(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(__file__).parent.glob("*.py")
            }
        ),
    }


def implementation(obj):
    path = inspect.getsourcefile(obj)
    return {
        "symbol": f"{obj.__module__}:{obj.__qualname__}",
        "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None,
    }


def run(config, directory: Path, label="run"):
    validate(config)
    directory.mkdir(parents=True, exist_ok=False)
    context = Context(
        str(uuid4()), str(directory.resolve()), label, config.get("seed", 42), config["target"]
    )
    result = {
        "version": 1,
        "run_id": context.run_id,
        "label": label,
        "status": "error",
        "started_at": now(),
        "target": config["target"],
        "phases": [],
        **provenance(),
    }
    save(directory / "scenario.json", config)
    result["scenario_sha256"] = digest(config)
    plan = logical_plan(config)
    result["logical_manifest_sha256"] = digest(plan)
    save(directory / "logical-manifest.json", plan)
    adapter = None
    records, acknowledgements = [], []
    rows = []
    pool = ThreadPoolExecutor(max_workers=1)
    fault = None
    fault_fired = False
    warm = config.get("warmup_seconds", 0)

    def phase(name):
        result["phases"].append({"name": name, "at": now()})
        save(directory / "result.json", result)

    try:
        if warm:
            phase("warmup")
            warm_config = copy.deepcopy(config)
            warm_config.update(
                warmup_seconds=0, phases=[{"seconds": warm, "rate": config["phases"][0]["rate"]}]
            )
            warm_config["hooks"] = {
                k: v for k, v in config.get("hooks", {}).items() if k != "fault"
            }
            warm_result = run(warm_config, directory / "warmup", "warmup")
            result["warmup_run_id"] = warm_result["run_id"]
            if warm_result["status"] != "pass":
                result["status"] = "inconclusive"
                result["reason"] = "warmup did not pass; measured load was not started"
                return result
        factory = symbol(config["adapter"])
        result["adapter_implementation"] = implementation(factory)
        adapter = factory(config.get("options", {}))
        phase("prepare")
        adapter.prepare(context)
        hook(config, "before_load", context)
        phase("load")
        started = time.monotonic()
        duration = sum(p["seconds"] for p in config["phases"])
        fault_spec = config.get("hooks", {}).get("fault")
        fault_at = fault_spec.get("at_seconds", 0) if fault_spec is not None else None

        def tick():
            nonlocal fault, fault_fired
            if fault_at is not None and not fault_fired and time.monotonic() - started >= fault_at:
                fault_fired = True
                phase("fault")
                fault = pool.submit(hook, config, "fault", context)
            if fault is not None and fault.done():
                fault.result()

        with (directory / "acknowledgements.jsonl").open("x") as ack_log:

            def acknowledged(ack):
                ack = dict(ack, acknowledged_at=now(), at_seconds=time.monotonic() - started)
                acknowledgements.append(ack)
                ack_log.write(json.dumps(ack, sort_keys=True) + "\n")
                ack_log.flush()

            with (directory / "manifest.jsonl").open("x") as manifest:
                for item in plan:
                    target = started + item["at_seconds"]
                    while time.monotonic() < target:
                        tick()
                        adapter.poll(min(0.01, max(0, target - time.monotonic())))
                    tick()
                    record = dict(
                        item,
                        id=str(uuid5(UUID(context.run_id), str(item["index"]))),
                        run_id=context.run_id,
                        produced_at=now(),
                    )
                    retry_until = time.monotonic() + config.get("ack_timeout_seconds", 30)
                    while True:
                        try:
                            adapter.send(record, acknowledged)
                            break
                        except BufferError:
                            if time.monotonic() >= retry_until:
                                raise TimeoutError("producer queue remained full") from None
                            tick()
                            adapter.poll(0.01)
                    record["submitted_at_seconds"] = time.monotonic() - started
                    records.append(record)
                    manifest.write(json.dumps(record, sort_keys=True) + "\n")
                    manifest.flush()
                    adapter.poll(0)
                # Honor the entire last phase and faults after the last scheduled record.
                while time.monotonic() - started < duration:
                    tick()
                    adapter.poll(min(0.01, max(0, duration - (time.monotonic() - started))))
                tick()
            pending = adapter.flush(config.get("ack_timeout_seconds", 30))
        if fault is not None:
            fault.result()
        successful = [a for a in acknowledgements if not a.get("error")]
        max_late = config.get("max_lateness_seconds", 0.5)
        on_time = sum(r["submitted_at_seconds"] <= r["at_seconds"] + max_late for r in records)
        in_window = sum(a["at_seconds"] <= duration for a in successful)
        ack_ids = [a["id"] for a in acknowledgements]
        record_ids = {r["id"] for r in records}
        delivery_valid = (
            not pending
            and len(ack_ids) == len(set(ack_ids))
            and set(ack_ids) == record_ids
            and len(successful) == len(records)
        )
        fraction = on_time / len(plan)
        result["load"] = {
            "scheduled": len(plan),
            "submitted": len(records),
            "acknowledged": len(successful),
            "on_time_fraction": fraction,
            "acknowledged_in_window": in_window,
            "acknowledged_in_window_per_second": in_window / duration,
            "scheduled_window_seconds": duration,
            "delivery_valid": delivery_valid,
            "valid": delivery_valid and fraction >= config.get("min_achieved_fraction", 0.95),
        }
        phase("drain")
        drain_start = time.monotonic()
        expected = symbol(config["expected"]) if config.get("expected") else lambda p: p
        result["checker_implementation"] = implementation(expected)
        deadline = drain_start + config.get("drain_seconds", 30)
        # Sample through the deadline: matching once can precede a delayed duplicate.
        with (directory / "observations.jsonl").open("x") as observations:
            while True:
                rows = adapter.observe(context)
                check = reconcile(records, acknowledgements, rows, expected)
                observations.write(json.dumps({"at": now(), "rows": rows}, sort_keys=True) + "\n")
                observations.flush()
                if time.monotonic() >= deadline:
                    break
                time.sleep(
                    max(0, min(config.get("poll_seconds", 0.1), deadline - time.monotonic()))
                )
        result["drain_seconds"] = time.monotonic() - drain_start
        phase("validation")
        result["checks"] = check
        result["threshold_failures"] = []
        thresholds = config.get("thresholds", {})
        if "p95_seconds" in thresholds and check["p95_seconds"] is not None:
            if check["p95_seconds"] > thresholds["p95_seconds"]:
                result["threshold_failures"].append("p95_seconds")
        # Drain completion time is first complete snapshot, independent of observation deadline.
        snapshots = [
            json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()
        ]
        first_complete = next(
            (
                s
                for s in snapshots
                if reconcile(records, acknowledgements, s["rows"], expected)["correct"]
            ),
            None,
        )
        completed_after = (
            (
                datetime.fromisoformat(first_complete["at"])
                - datetime.fromisoformat(result["phases"][-2]["at"])
            ).total_seconds()
            if first_complete
            else None
        )
        result["completion_after_load_seconds"] = completed_after
        if "drain_seconds" in thresholds and completed_after is not None:
            if completed_after > thresholds["drain_seconds"]:
                result["threshold_failures"].append("drain_seconds")
        if not result["load"]["valid"] or check["invalid_timing"]:
            result["status"] = "inconclusive"
        elif not check["correct"] or result["threshold_failures"]:
            result["status"] = "fail"
        else:
            result["status"] = "pass"
    except (Exception, KeyboardInterrupt) as exc:
        # Driver messages and hook argv can contain secrets; retain the error class only.
        result["error_type"] = type(exc).__name__
        result["status"] = "error"
    finally:
        phase("cleanup")
        for action in (
            lambda: pool.shutdown(wait=True),
            lambda: adapter.close() if adapter is not None else None,
            lambda: hook(config, "cleanup", context),
        ):
            try:
                action()
            except Exception as exc:
                result.setdefault("cleanup_errors", []).append(type(exc).__name__)
                result["status"] = "error"
        result["finished_at"] = now()
        result["eligible_for_comparison"] = result["status"] == "pass"
        result["files_sha256"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.iterdir()
            if p.is_file() and p.name != "result.json"
        }
        save(directory / "result.json", result)
    return result
