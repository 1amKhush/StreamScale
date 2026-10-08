"""Pilot, frozen 27-cell matrix, exact reconciliation, and restart-safe evidence export."""

import argparse
import csv
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from prometheus_client.parser import text_string_to_metric_families

from streamscale.run_artifacts import (
    read_acknowledgements,
    revision_snapshot,
    utc_now,
    verify_manifest,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/k8s"))

from access import fetch, forward  # noqa: E402
from addons import main as install_addons  # noqa: E402
from manage import (  # noqa: E402
    CLUSTER,
    NAMESPACE,
    TOOLS,
    build,
    call,
    cluster,
    deploy,
    kubectl,
    workflow,
)  # noqa: E402
from scaling import MODES, inspect, mode_snapshot  # noqa: E402

from experiments.metrics import correct, epoch, quantity, summarize  # noqa: E402

DESTINATION = ROOT / "artifacts/experiments"
LATEST = DESTINATION / "latest.path"


def fingerprint():
    digest = hashlib.sha256(revision_snapshot()["source_sha256"].encode())
    paths = [ROOT / "experiments" / name for name in ("run.py", "metrics.py", "protocol.json")]
    paths += [
        path
        for path in (ROOT / "deploy/k8s").rglob("*")
        if path.suffix in {".py", ".json", ".yaml"}
    ]
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def plan(protocol):
    cells = [
        {
            "mode": mode,
            "scenario": scenario,
            "repetition": repeat,
            "key": f"{scenario}-{mode}-{repeat}",
        }
        for repeat in range(1, protocol["repetitions"] + 1)
        for scenario in protocol["scenarios"]
        for mode in MODES
    ]
    random.Random(protocol["order_seed"]).shuffle(cells)
    return cells


def freeze(protocol, capacity):
    if capacity <= 0:
        raise ValueError("Pilot capacity must be positive")
    steady = max(2, int(capacity * protocol["steady_capacity_factor"]) // 2 * 2)
    surge = max(
        steady + 2,
        min(
            protocol["maximum_surge_rate"],
            int(capacity * protocol["surge_capacity_factor"]) // 2 * 2,
        ),
    )
    frozen = json.loads(json.dumps(protocol))
    frozen["pilot_capacity_per_second"] = capacity
    frozen["rates"] = {"steady": steady, "surge": surge}
    for scenario in frozen["scenarios"].values():
        scenario["schedule"] = [
            [seconds, frozen["rates"][rate]] for seconds, rate in scenario["phases"]
        ]
    frozen["frozen_at"] = utc_now()
    return frozen


def raw_metrics(payload, run_id):
    values = {}
    for family in text_string_to_metric_families(payload):
        for sample in family.samples:
            if sample.labels.get("run_id") == run_id:
                values.setdefault(sample.name, []).append(sample)
    lag = {
        sample.labels["partition"]: sample.value
        for sample in values.get("streamscale_kafka_lag", [])
    }
    success = values.get("streamscale_exporter_last_success_unixtime", [])
    return {
        "lag": lag if set(lag) == {"0", "1", "2"} else None,
        "exporter_sample_at": success[0].value if success else None,
        "members": sum(sample.value for sample in values.get("streamscale_consumer_replicas", [])),
    }


def current_exporter(name, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = json.loads(
            kubectl(
                "get", "pods", "-l", "app=streamscale-exporter", "-o", "json", capture=True
            ).stdout
        )["items"]
        for pod in pods:
            if (
                pod["metadata"].get("annotations", {}).get("streamscale/run-name") == name
                and pod["status"].get("phase") == "Running"
                and not pod["metadata"].get("deletionTimestamp")
            ):
                return pod["metadata"]["name"]
        time.sleep(0.2)
    raise RuntimeError("The new execution's exporter did not start")


def pod_resources(pods):
    wanted = {pod["metadata"]["name"] for pod in pods}
    result = kubectl(
        "get",
        "--raw",
        f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods",
        capture=True,
        check=False,
    )
    if result.returncode:
        return None
    rows = [pod for pod in json.loads(result.stdout)["items"] if pod["metadata"]["name"] in wanted]
    if not rows:
        return None
    return {
        "cpu_cores": sum(
            quantity(container["usage"]["cpu"], "cpu")
            for pod in rows
            for container in pod["containers"]
        ),
        "memory_bytes": sum(
            quantity(container["usage"]["memory"], "memory")
            for pod in rows
            for container in pod["containers"]
        ),
        "resource_api_samples": rows,
    }


def copy_run(name, destination):
    for filename in ("run.json", "manifest.jsonl", "acknowledgements.jsonl", "inspection.json"):
        result = kubectl(
            "exec",
            "deployment/streamscale-consumer",
            "--",
            "/bin/cat",
            f"/runs/{name}/{filename}",
            capture=True,
        )
        (destination / filename).write_text(result.stdout)
    run = json.loads((destination / "run.json").read_text())
    verify_manifest(destination, run)
    read_acknowledgements(destination, run)
    return run


def scrape_history(run, samples, destination):
    from urllib.parse import urlencode

    from verify import query

    with forward("prometheus") as endpoint:
        expressions = [
            name + '{run_id="' + run["run_id"] + '"}'
            for name in (
                "streamscale_kafka_lag",
                "streamscale_consumer_replicas",
                "streamscale_accepted_total",
                "streamscale_duplicate_total",
                "streamscale_invalid_total",
                "streamscale_dlq_total",
                "streamscale_failed_total",
                "streamscale_end_to_end_seconds_bucket",
                "streamscale_db_seconds_bucket",
            )
        ]
        results = {}
        for expression in expressions:
            params = {
                "query": expression,
                "start": epoch(run["publish_started_at"]),
                "end": samples[-1]["time"],
                "step": 1,
            }
            results[expression] = json.loads(
                fetch(endpoint + "/api/v1/query_range?" + urlencode(params))
            )
        write_json(destination / "prometheus-range.json", results)
        failed = query(
            endpoint, 'sum(increase(streamscale_failed_total{run_id="' + run["run_id"] + '"}[30m]))'
        )
        write_json(destination / "handling-failures.json", {"samples": failed})


def capture(metadata, cell, configuration, campaign):
    schedule = configuration["schedule"]
    count = sum(seconds * rate for seconds, rate in schedule)
    if count % 2:
        raise RuntimeError("The frozen schedule must contain an even number of logical events")
    args = SimpleNamespace(
        mode=cell["mode"],
        scenario=cell["scenario"],
        seed=cell["seed"],
        orders=count // 2,
        profile=configuration["profile"],
        paced=configuration["paced"],
        start_delay=8,
        schedule=schedule,
        configuration=configuration,
    )
    info = deploy(metadata, args, wait=False)
    destination = campaign / info["run_name"]
    destination.mkdir()
    write_json(destination / "deployment.json", info)
    write_json(destination / "configuration.json", configuration)
    write_json(
        campaign / "active-run.json",
        {"cell": cell, "deployment": info, "directory": str(destination)},
    )
    print(
        f"Started {configuration['phase']} {cell['mode']} / {cell['scenario']}: {info['run_name']}",
        flush=True,
    )
    run = None
    zeros = 0
    last_fresh = None
    next_resource = 0
    samples = []
    began = time.monotonic()
    deadline = (
        began
        + 180
        + sum(seconds for seconds, _ in schedule)
        + configuration["drain_timeout_seconds"]
    )
    pod = current_exporter(info["run_name"])
    with (
        forward("exporter", resource="pod/" + pod) as endpoint,
        (destination / "samples.jsonl").open("w") as log,
        (destination / "exporter-raw.txt").open("w") as raw,
    ):
        while time.monotonic() < deadline:
            iteration = time.monotonic()
            snapshot = mode_snapshot(cell["mode"])
            sample = snapshot | {
                "time": time.time(),
                "lag": None,
                "exporter_sample_at": None,
                "cpu_cores": None,
                "memory_bytes": None,
            }
            job = json.loads(
                kubectl(
                    "get",
                    "job",
                    "streamscale-workload-" + info["run_name"],
                    "-o",
                    "json",
                    capture=True,
                ).stdout
            )
            if job["status"].get("failed"):
                raise RuntimeError("The experiment publisher Job failed")
            if run is None or run.get("status") != "published":
                result = kubectl(
                    "exec",
                    "deployment/streamscale-exporter",
                    "--",
                    "/bin/cat",
                    "/runs/" + info["run_name"] + "/run.json",
                    capture=True,
                    check=False,
                )
                if result.returncode == 0:
                    run = json.loads(result.stdout)
            if run and run["status"] in {"publishing", "published"}:
                try:
                    payload = fetch(endpoint + "/metrics").decode()
                    raw.write(f"# captured_at {sample['time']}\n" + payload)
                    sample.update(raw_metrics(payload, run["run_id"]))
                    if (
                        sample["exporter_sample_at"] is None
                        or sample["time"] - sample["exporter_sample_at"] > 3
                    ):
                        sample["lag"] = None
                except OSError as error:
                    sample["metric_error"] = type(error).__name__
            if iteration >= next_resource:
                next_resource = iteration + configuration["resource_interval_seconds"]
                resource = pod_resources(snapshot["pods"])
                if resource:
                    sample.update(resource)
            samples.append(sample)
            log.write(json.dumps(sample) + "\n")
            log.flush()
            if sample["lag"] is None:
                zeros = 0
            elif sample["exporter_sample_at"] != last_fresh:
                if last_fresh is not None and sample["exporter_sample_at"] - last_fresh > 2:
                    zeros = 0
                last_fresh = sample["exporter_sample_at"]
                zeros = zeros + 1 if sum(sample["lag"].values()) == 0 else 0
            if (
                run
                and run["status"] == "published"
                and job["status"].get("succeeded")
                and zeros >= 5
                and sample["time"]
                >= epoch(run["publish_started_at"]) + sum(seconds for seconds, _ in schedule)
            ):
                break
            if len(samples) % 15 == 0:
                lag_value = sum(sample["lag"].values()) if sample["lag"] else "waiting"
                print(
                    f"  {info['run_name']}: ready={sample['ready']} lag={lag_value}",
                    flush=True,
                )
            time.sleep(
                max(0, configuration["sample_interval_seconds"] - (time.monotonic() - iteration))
            )
        else:
            raise RuntimeError("The run did not drain before its experiment deadline")
    before = inspect("/runs/" + info["run_name"])
    if before is None:
        raise RuntimeError("Pre-restart SQL/DLQ reconciliation failed")
    write_json(destination / "before-restart-inspection.json", before)
    run = copy_run(info["run_name"], destination)
    result = kubectl(
        "exec",
        "deployment/streamscale-consumer",
        "--",
        "/app/.venv/bin/streamscale",
        "measurements",
        "--run-dir",
        "/runs/" + info["run_name"],
        capture=True,
    )
    measurements = json.loads(result.stdout)
    write_json(destination / "sql-measurements.json", measurements)
    scrape_history(run, samples, destination)
    # Restart is after the measurement/drain window, so it checks correctness without
    # contaminating resource, latency or throughput comparisons.
    restarted_at = utc_now()
    kubectl("rollout", "restart", "deployment/streamscale-consumer")
    kubectl("rollout", "status", "deployment/streamscale-consumer", "--timeout=180s")
    after = inspect("/runs/" + info["run_name"])
    if after is None:
        raise RuntimeError("Controlled-restart reconciliation failed")
    write_json(
        destination / "restart.json",
        {"started_at": restarted_at, "ended_at": utc_now(), "inspection": after},
    )
    row = summarize(run, measurements, samples, before, after, configuration)
    row["image_digest"] = metadata["image_id"]
    row["cell_key"] = cell["key"]
    row["artifact_directory"] = str(destination.relative_to(ROOT))
    write_json(destination / "summary.json", row)
    checksums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in destination.iterdir()
        if path.is_file()
    }
    write_json(destination / "checksums.json", checksums)
    (campaign / "active-run.json").unlink()
    print(
        f"Completed {run['run_id']}: {row['status']}, facts={row['unique_facts']}, "
        f"p95={row['p95_end_to_end_seconds']}s",
        flush=True,
    )
    return row


def verify_evidence(campaign, rows):
    """Refuse reuse when preserved raw evidence has been changed or lost."""
    for row in rows:
        directory = ROOT / row["artifact_directory"]
        if directory.parent != campaign:
            raise RuntimeError("Run evidence is outside this campaign")
        checksums = json.loads((directory / "checksums.json").read_text())
        required = {
            "run.json",
            "manifest.jsonl",
            "acknowledgements.jsonl",
            "samples.jsonl",
            "sql-measurements.json",
            "before-restart-inspection.json",
            "restart.json",
            "summary.json",
        }
        if not required <= checksums.keys():
            raise RuntimeError("Incomplete raw evidence checksum manifest")
        for name, checksum in checksums.items():
            if (
                Path(name).name != name
                or hashlib.sha256((directory / name).read_bytes()).hexdigest() != checksum
            ):
                raise RuntimeError(f"Raw evidence checksum mismatch: {directory / name}")
        if json.loads((directory / "summary.json").read_text()) != row:
            raise RuntimeError("Campaign summary differs from preserved run evidence")


def validate_matrix(state):
    measured = [row for row in state["rows"] if row["phase"] == "measured"]
    expected = {cell["key"] for cell in state["plan"]}
    if len(measured) != 27 or {row["cell_key"] for row in measured} != expected:
        raise RuntimeError("The complete 27-cell measured matrix is required")
    if any(not row["performance_eligible"] for row in measured):
        raise RuntimeError("A failed measured run cannot enter the performance comparison")
    if len({row["run_id"] for row in state["rows"]}) != len(state["rows"]):
        raise RuntimeError("Executions must have distinct run IDs")
    if {row["image_digest"] for row in state["rows"]} != {state["image"]["image_id"]}:
        raise RuntimeError("The consumer image changed during the campaign")
    for scenario in state["protocol"]["scenarios"]:
        if len({row["logical_sha256"] for row in measured if row["scenario"] == scenario}) != 1:
            raise RuntimeError("Compared scenario workloads differ logically")


def export_csv(campaign, state):
    rows = state["rows"]
    for name, selected in (
        ("all-runs.csv", rows),
        ("summary.csv", [row for row in rows if row["phase"] == "measured"]),
        (
            "comparison.csv",
            [row for row in rows if row["phase"] == "measured" and row["performance_eligible"]],
        ),
        ("failed-attempts.csv", state["failures"]),
    ):
        fields = sorted({key for row in selected for key in row}) or ["failure_reason"]
        with (campaign / name).open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(selected)
    write_json(campaign / "campaign.json", state)


def environment():
    return {
        "captured_at": utc_now(),
        "docker": json.loads(
            call(["docker", "info", "--format", "{{json .}}"], capture=True).stdout
        ),
        "nodes": json.loads(kubectl("get", "nodes", "-o", "json", capture=True).stdout),
        "pods": json.loads(kubectl("get", "pods", "-o", "json", capture=True).stdout),
        "helm_values": json.loads(
            call(
                [
                    TOOLS / "helm",
                    "get",
                    "values",
                    "streamscale",
                    "--all",
                    "-n",
                    NAMESPACE,
                    "--kube-context",
                    f"kind-{CLUSTER}",
                    "-o",
                    "json",
                ],
                capture=True,
            ).stdout
        ),
    }


def configuration(protocol, phase, schedule, repetition=0):
    return {
        "protocol_version": protocol["protocol_version"],
        "phase": phase,
        "repetition": repetition,
        "schedule": schedule,
        "profile": "correctness" if phase == "measured" else "normal",
        "paced": phase != "pilot",
        "sample_interval_seconds": protocol["sample_interval_seconds"],
        "resource_interval_seconds": protocol["resource_interval_seconds"],
        "drain_timeout_seconds": protocol["drain_timeout_seconds"],
    }


def recover_active(campaign, state):
    active_file = campaign / "active-run.json"
    if not active_file.exists():
        return
    active = json.loads(active_file.read_text())
    directory = Path(active["directory"])
    config = json.loads((directory / "configuration.json").read_text())
    cell = active["cell"]
    args = SimpleNamespace(
        mode=cell["mode"],
        scenario=cell["scenario"],
        seed=cell["seed"],
        orders=sum(seconds * rate for seconds, rate in config["schedule"]) // 2,
        profile=config["profile"],
        paced=config["paced"],
        start_delay=8,
        schedule=config["schedule"],
        configuration=config,
    )
    print("Recovering the interrupted execution before any new publication", flush=True)
    deploy(state["image"], args, wait=False, run_name=active["deployment"]["run_name"])
    deadline = time.monotonic() + config["drain_timeout_seconds"] + 180
    while time.monotonic() < deadline:
        result = inspect("/runs/" + active["deployment"]["run_name"])
        if result and correct(result):
            copy_run(active["deployment"]["run_name"], directory)
            write_json(directory / "recovery-inspection.json", result)
            failure = {
                "run_name": active["deployment"]["run_name"],
                "cell_key": cell["key"],
                "phase": config["phase"],
                "mode": cell["mode"],
                "scenario": cell["scenario"],
                "performance_eligible": 0,
                "failure_reason": "interrupted measurement; recovered and excluded",
                "artifact_directory": str(directory.relative_to(ROOT)),
                "recovered_at": utc_now(),
            }
            if not any(row.get("run_name") == failure["run_name"] for row in state["failures"]):
                state["failures"].append(failure)
            active_file.unlink()
            export_csv(campaign, state)
            return
        time.sleep(2)
    raise RuntimeError(
        "Interrupted execution has not reconciled; its evidence and consumers are retained"
    )


def record_capture(campaign, state, cell, config):
    try:
        row = capture(state["image"], cell, config, campaign)
        state["rows"].append(row)
        export_csv(campaign, state)
        if not row["performance_eligible"]:
            raise RuntimeError("Run failed measurement eligibility: " + row["failure_reason"])
        return row
    except Exception as error:
        state["status"] = "interrupted"
        write_json(
            campaign / "last-error.json",
            {"at": utc_now(), "cell": cell, "phase": config["phase"], "error": str(error)},
        )
        export_csv(campaign, state)
        raise


def restore_demo(metadata):
    deploy(
        metadata,
        SimpleNamespace(
            mode="fixed",
            scenario="steady",
            seed=42,
            orders=200,
            profile="correctness",
            paced=False,
            start_delay=0,
        ),
    )
    from verify import main as verify_demo

    verify_demo()


def run_campaign(args):
    protocol = json.loads((ROOT / "experiments/protocol.json").read_text())
    current = fingerprint()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    if args.resume and LATEST.exists():
        campaign = Path(LATEST.read_text().strip())
        state = json.loads((campaign / "campaign.json").read_text())
        if state["fingerprint"] != current:
            raise RuntimeError(
                "Experiment code/configuration changed. Retain the old campaign and start: "
                "./streamscale.sh experiments --fresh"
            )
        verify_evidence(campaign, state["rows"])
        if state["status"] == "complete":
            validate_matrix(state)
            print(f"Verified retained 27-run matrix: {campaign}", flush=True)
            return
        cluster()
        # Preserve the exact original image even when another demo has built a newer tag.
        call(["docker", "tag", state["image"]["image_id"], state["image"]["image"]])
    else:
        metadata = build()
        cluster()
        campaign = DESTINATION / (
            "campaign-" + utc_now()[:19].replace(":", "").replace("-", "") + "-" + uuid4().hex[:6]
        )
        campaign.mkdir()
        state = {
            "started_at": utc_now(),
            "status": "running",
            "fingerprint": current,
            "protocol": protocol,
            "plan": plan(protocol),
            "image": metadata,
            "rows": [],
            "failures": [],
        }
        write_json(campaign / "campaign.json", state)
        LATEST.write_text(str(campaign) + "\n")
    call([TOOLS / "kind", "load", "docker-image", state["image"]["image"], "--name", CLUSTER])
    install_addons()
    if not (campaign / "environment.json").exists():
        # Limit host metadata to reproducibility fields, excluding registry/proxy/user details.
        snapshot = environment()
        snapshot["docker"] = {
            key: snapshot["docker"].get(key)
            for key in ("ServerVersion", "NCPU", "MemTotal", "Architecture", "OperatingSystem")
        }
        write_json(campaign / "environment.json", snapshot)
    recover_active(campaign, state)
    frozen_path = campaign / "frozen-protocol.json"
    if frozen_path.exists():
        frozen = json.loads(frozen_path.read_text())
    else:
        pilot_cell = {"mode": "fixed", "scenario": "steady", "seed": 99, "key": "pilot"}
        if not any(row["cell_key"] == "pilot-warmup" for row in state["rows"]):
            record_capture(
                campaign,
                state,
                pilot_cell | {"key": "pilot-warmup"},
                configuration(protocol, "warmup", [[protocol["warmup_seconds"], 30]]),
            )
        pilot = next((row for row in state["rows"] if row["phase"] == "pilot"), None)
        if pilot is None:
            pilot = record_capture(
                campaign,
                state,
                pilot_cell,
                configuration(protocol, "pilot", [[10, protocol["pilot_orders"] // 5]]),
            )
        frozen = freeze(protocol, pilot["throughput_processing_per_second"])
        frozen["pilot_run_id"] = pilot["run_id"]
        write_json(frozen_path, frozen)
    print(
        f"Frozen protocol {frozen['protocol_version']}: rates={frozen['rates']}; "
        "27 measured executions",
        flush=True,
    )
    completed = {row["cell_key"] for row in state["rows"] if row["performance_eligible"]}
    for cell in state["plan"]:
        if cell["key"] in completed:
            continue
        scenario = frozen["scenarios"][cell["scenario"]]
        cell = cell | {"seed": scenario["seed"]}
        warmup = record_capture(
            campaign,
            state,
            cell | {"key": cell["key"] + "-warmup"},
            configuration(
                frozen,
                "warmup",
                [[frozen["warmup_seconds"], frozen["rates"]["steady"]]],
                cell["repetition"],
            ),
        )
        config = configuration(frozen, "measured", scenario["schedule"], cell["repetition"])
        config["warmup_run_id"] = warmup["run_id"]
        config["warmup_seconds"] = frozen["warmup_seconds"]
        config["load_transition_seconds"] = 15 if cell["scenario"] == "flash-sale" else 0
        if cell["scenario"] == "overload-recovery":
            config["recovery_transition_seconds"] = 35
        record_capture(campaign, state, cell, config)
        count = sum(
            row["phase"] == "measured" and row["performance_eligible"] for row in state["rows"]
        )
        print(f"Matrix progress: {count}/27 correct measured runs", flush=True)
    validate_matrix(state)
    verify_evidence(campaign, state["rows"])
    state["status"] = "complete"
    state["ended_at"] = utc_now()
    export_csv(campaign, state)
    restore_demo(state["image"])
    print(f"M7 complete: {campaign / 'summary.csv'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--resume", action="store_true", help="Reuse complete evidence or resume its frozen matrix"
    )
    group.add_argument(
        "--fresh", action="store_true", help="Create a new campaign, retaining prior evidence"
    )
    args = parser.parse_args()
    args.resume = not args.fresh
    with workflow():
        run_campaign(args)


if __name__ == "__main__":
    main()
