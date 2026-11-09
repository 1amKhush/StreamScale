"""Prepare once; separate, immutable held-out evaluation. No simulator scores."""

import argparse
import asyncio
import csv
import json
import os
import random
import shutil
import statistics
import subprocess
import time

from streamscale_console.cluster import ROOT, kubectl

from experiments.predictive.runtime import capture, driver, install, operation
from streamscale.predictive import DEFAULT_POLICY
from streamscale.predictive.model import digest, load, publish, train, versions
from streamscale.topics import MAX_REPLICAS, PARTITIONS

ARTIFACTS = ROOT / "artifacts/predictive"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False))
    os.replace(temp, path)


def fingerprint():
    paths = [
        ROOT / "src/streamscale",
        ROOT / "experiments/predictive",
        ROOT / "console/worker/streamscale_console_worker",
    ]
    files = [p for root in paths for p in root.rglob("*.py") if ".venv" not in p.parts]
    files.extend(
        [ROOT / "uv.lock", ROOT / "console/uv.lock", ROOT / "experiments/predictive/uv.lock"]
    )
    sources = {str(p.relative_to(ROOT)): p.read_text() for p in sorted(files)}
    deployment = json.loads(
        kubectl("get", "deployment", "streamscale-console-consumer", "-o", "json").stdout
    )
    resources = deployment["spec"]["template"]["spec"]["containers"][0]["resources"]
    node = json.loads(kubectl("get", "nodes", "-o", "json").stdout)["items"][0]
    return digest(
        {
            "sources": sources,
            "versions": versions(),
            "resources": resources,
            "partitions": PARTITIONS,
            "replica_bounds": [1, MAX_REPLICAS],
            "node": {
                key: node["status"]["nodeInfo"][key]
                for key in (
                    "architecture",
                    "kernelVersion",
                    "kubeletVersion",
                    "containerRuntimeVersion",
                    "osImage",
                )
            },
            "cpu": node["status"]["capacity"]["cpu"],
        }
    )


def config(family, seed, base=40, seconds=180):
    if seconds < 60 or seconds % 6:
        raise ValueError("Trace duration must be at least 60 seconds and divisible by six")
    rng = random.Random(seed)
    low = max(2, int(base * rng.uniform(0.30, 0.45)) // 2 * 2)
    high = max(low + 2, int(base * rng.uniform(6, 9)) // 2 * 2)
    overload = max(high, int(base * rng.uniform(13, 16)) // 2 * 2)
    # Six phases fit the publisher limit and retain a full 60s of history.
    step = seconds // 6
    patterns = {
        "steady": [low] * 6,
        "ramp": [low, low, low * 2, low * 6, low * 10, low * 3],
        "repeated-burst": [low, high, low, high, low, high],
        "abrupt-step": [low, low, low, high, high, low],
        "flash-sale": [low, low, low, high, high, low],
        "overload-recovery": [overload, overload, overload, overload, low, low],
    }
    schedule = [[step, min(2000, rate)] for rate in patterns[family]]
    if family == "overload-recovery":
        # Give all modes observed warm-up, then a short saturated surge and a
        # recovery tail. Sustaining 13–16x single-consumer capacity for 120s would
        # exceed the fixed baseline's bounded drain budget before reconciliation.
        burst = max(1, step // 3)
        schedule = [
            [2 * step, low],
            [burst, min(2000, overload)],
            [burst, min(2000, overload)],
            [step - 2 * burst, min(2000, overload)],
            [step, low],
            [seconds - 4 * step, low],
        ]
    return {
        "preset": "custom",
        "scenario": "steady",
        "seed": seed,
        "schedule": schedule,
        "faults": {"malformed_pct": 0, "duplicate_pct": 0, "late_pct": 0},
        "family": family,
    }


def summary(run):
    points = run["points"]
    elapsed = sum(max(0, b["t"] - a["t"]) for a, b in zip(points, points[1:], strict=False))
    replica_seconds = sum(
        (b["t"] - a["t"]) * a["ready"]
        for a, b in zip(points, points[1:], strict=False)
        if a.get("ready") is not None and 0 < b["t"] - a["t"] <= 5
    )
    predictive = [p.get("predictive") or {} for p in points]
    fallback = sum(
        (b["t"] - a["t"])
        for a, b in zip(points, points[1:], strict=False)
        if (a.get("predictive") or {}).get("status") in {"fallback", "warming up"}
    )
    latency = run["record"]["reconciliation"]["latency_s"]["p95"]
    schedule = run.get("config", {}).get("schedule", [])
    peak = max((rate for _, rate in schedule), default=0)
    last_peak = max((i for i, (_, rate) in enumerate(schedule) if rate == peak), default=-1)
    recovery_start = sum(seconds for seconds, _ in schedule[: last_peak + 1])
    first = points[0]["t"] if points else 0
    drained = next(
        (p["t"] for p in points if p["t"] >= first + recovery_start and p.get("lag_total") == 0),
        None,
    )
    forecast_samples = sum(p.get("forecast_rate") is not None for p in predictive)
    return {
        "forecast_samples": forecast_samples,
        "performance_eligible": (
            run["record"]["reconciliation_status"] == "passed"
            and (run.get("mode") != "ml-predictive" or forecast_samples > 0)
        ),
        "above_latency_target": run["record"]["reconciliation"]["latency_s"].get("above_target"),
        "recovery_seconds": max(0, drained - first - recovery_start)
        if drained is not None and run.get("family") == "overload-recovery"
        else None,
        "measurement_seconds": elapsed,
        "replica_coverage": sum(
            b["t"] - a["t"]
            for a, b in zip(points, points[1:], strict=False)
            if a.get("ready") is not None and 0 < b["t"] - a["t"] <= 5
        )
        / elapsed
        if elapsed
        else 0,
        "session": run["id"],
        "p95": latency,
        "target_met": latency is not None and latency <= 2,
        "replica_seconds": replica_seconds,
        "peak_lag": max((p["lag_total"] or 0 for p in points), default=0),
        "scaling_churn": sum(
            a.get("desired") != b.get("desired") for a, b in zip(points, points[1:], strict=False)
        ),
        "fallback_fraction": fallback / elapsed if elapsed else None,
        "correct": run["record"]["reconciliation_status"] == "passed",
        "model_ids": sorted({p["model_id"] for p in predictive if p.get("model_id")}),
    }


async def retained(console, directory, key, configuration, mode, replicas=None):
    path = directory / f"{key}.json"
    if path.exists():
        print(f"[predictive] reuse {key}", flush=True)
        return json.loads(path.read_text())
    events = sum(seconds * rate for seconds, rate in configuration["schedule"])
    if shutil.disk_usage(ROOT).free < events * 4096 + 512 * 1024**2:
        raise RuntimeError(
            "Not enough disk space for the next retained capture; free space and resume"
        )
    print(f"[predictive] capture {key}", flush=True)
    run = await capture(console, configuration, mode, replicas)
    save(path, run)
    return run


def calibration(runs):
    capacities = {}
    actuation = []
    handling = []
    for n in range(1, MAX_REPLICAS + 1):
        rates = []
        for run in runs:
            if run["replicas"] != n:
                continue
            rows = run["observations"]
            handling.extend(p["db_p95"] for p in run["points"] if p.get("db_p95") is not None)
            for a, b in zip(rows, rows[1:], strict=False):
                dt = b["t"] - a["t"]
                if (
                    a["stable"]
                    and b["stable"]
                    and a["ready"] == n
                    and b["ready"] == n
                    and min(a["lag"]) > 5
                    and 0 < dt <= 5
                ):
                    rates.append(
                        sum(y - x for x, y in zip(a["position"], b["position"], strict=True)) / dt
                    )
            if rows:
                first = next((r["t"] for r in rows if r["ready"] == n and r["stable"]), None)
                if first:
                    actuation.append(max(0, first - run["actuation_started_at"]))
        if not rates or max(rates) <= 0:
            raise RuntimeError(f"Insufficient saturated calibration for {n} replicas")
        capacities[n] = statistics.median(rates)
        if capacities[n] <= 0:
            raise RuntimeError(f"No positive median capacity for {n} replicas")
    return {
        "capacity": capacities,
        "actuation_seconds": max(5, max(actuation, default=5)),
        "handling_seconds": max(handling) if handling else 0,
    }


async def prepare(console, directory, identity):
    folder = directory / "model"
    try:
        identifier, _, _ = load(folder, identity)
        install(folder)
        print(f"[predictive] reusing validated model/policy {identifier}", flush=True)
        return
    except (OSError, ValueError, KeyError):
        pass
    free = shutil.disk_usage(ROOT).free
    if free < 5 * 1024**3:
        raise RuntimeError(
            f"Predictive preparation needs at least 5 GiB free for retained live evidence; "
            f"only {free / 1024**3:.1f} GiB is available. "
            "Free space and rerun; nothing was deleted."
        )
    training, validation = [], []
    # Separate saturated traces measure capacity at each size, never extrapolate n*capacity(1).
    calibrated = []
    for n in range(1, MAX_REPLICAS + 1):
        run = await retained(
            console,
            directory,
            f"calibration-{n}",
            config("steady", 9000 + n, seconds=120) | {"schedule": [[20, 2000]]},
            "manual",
            n,
        )
        calibrated.append(run | {"replicas": n})
    measured = calibration(calibrated)
    base = measured["capacity"][1]
    for split, count, offset, destination in (
        ("train", 3, 10000, training),
        ("validation", 1, 20000, validation),
    ):
        for family in ("steady", "ramp", "repeated-burst", "abrupt-step"):
            for repeat in range(count):
                seed = (
                    offset
                    + repeat * 100
                    + ("steady", "ramp", "repeated-burst", "abrupt-step").index(family)
                )
                destination.append(
                    await retained(
                        console,
                        directory,
                        f"{split}-{family}-{repeat}",
                        config(family, seed, base),
                        "manual",
                        MAX_REPLICAS,
                    )
                )
    models, scores = train(training, validation)
    metadata = {
        "fingerprint": identity,
        "calibration": measured,
        "policy": DEFAULT_POLICY,
        "scores": scores,
        "training_ids": [r["id"] for r in training],
        "validation_ids": [r["id"] for r in validation],
        "data_sha256": digest(training + validation),
        "tuning": "pending",
        "git": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    }
    # Candidates are installed separately; the last accepted local model is untouched.
    candidate = directory / "candidate"
    publish(candidate, models, metadata)
    install(candidate)
    from experiments.predictive.tuning import tune

    policy, evidence = await tune(console, directory, models, metadata, base)
    metadata |= {"policy": policy, "tuning": evidence}
    identifier = publish(folder, models, metadata)
    install(folder)
    print(f"[predictive] prepared {identifier}; tuning={evidence['status']}", flush=True)


async def evaluate(console, directory):
    load(directory / "model")
    install(directory / "model")
    _, _, metadata = load(directory / "model")
    base = metadata["calibration"]["capacity"].get("1", metadata["calibration"]["capacity"].get(1))
    if (
        shutil.disk_usage(ROOT).free < 5 * 1024**3
        and not (directory / "evaluation/complete.json").exists()
    ):
        raise RuntimeError("Held-out evaluation needs at least 5 GiB free for retained evidence")
    cells = [
        (family, mode, repeat)
        for family in ("steady", "flash-sale", "overload-recovery")
        for mode in ("fixed", "cpu-hpa", "lag-keda", "ml-predictive")
        for repeat in range(3)
    ]
    random.Random(424242).shuffle(cells)
    results = []
    for family, mode, repeat in cells:
        seed = 40000 + repeat * 100 + ("steady", "flash-sale", "overload-recovery").index(family)
        run = await retained(
            console,
            directory / "evaluation",
            f"{family}-{mode}-{repeat}",
            config(family, seed, base),
            mode,
        )
        results.append(summary(run) | {"family": family, "mode": mode, "repetition": repeat})
        save(directory / "evaluation" / "summary.json", results)
    with (directory / "evaluation/summary.csv").open("w") as output:
        writer = csv.DictWriter(output, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    report = [
        "# Held-out ML Predictive comparison",
        "",
        "Only correctness-passing live runs are included. Target misses remain visible.",
        "",
        "| Workload | Mode | Repeat | Exact p95 (s) | Replica-seconds | Target met |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        report.append(
            f"| {row['family']} | {row['mode']} | {row['repetition']} | "
            f"{row['p95']:.3f} | {row['replica_seconds']:.1f} | {row['target_met']} |"
        )
    (directory / "evaluation/report.md").write_text("\n".join(report) + "\n")
    save(
        directory / "evaluation" / "complete.json",
        {"runs": 36, "model_id": load(directory / "model")[0]},
    )


async def main_async(command):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    identity = fingerprint()
    directory = ARTIFACTS / identity
    async with driver() as console:
        try:
            if command == "prepare":
                await prepare(console, directory, identity)
                save(ARTIFACTS / "current.json", {"fingerprint": identity})
            else:
                load(directory / "model", identity)
                if command == "evaluate":
                    await evaluate(console, directory)
                else:
                    install(directory / "model")
                    run = await retained(
                        console,
                        directory / "demo",
                        str(time.time_ns()),
                        config("ramp", 50001),
                        "ml-predictive",
                    )
                    save(directory / "demo" / "latest.json", summary(run))
        finally:
            # An active interrupted sale continues in the agent; don't stop it for cleanup.
            sales = next(c for c in console.components if hasattr(c, "store"))
            if sales.record is None:
                await operation(console, "scaling.mode", {"mode": "fixed"})
            if command == "prepare" and (directory / "model/current.json").exists():
                install(directory / "model")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "evaluate", "demo"))
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.command))
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(f"Predictive {args.command} failed: {error}") from None


if __name__ == "__main__":
    main()
