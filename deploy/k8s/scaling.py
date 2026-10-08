"""Exercise each alternative controller and retain observed replica-bound evidence."""

import json
import time
from types import SimpleNamespace

from addons import main as install_addons
from manage import CLUSTER, OUTPUT, TOOLS, build, call, cluster, deploy, kubectl

from streamscale.run_artifacts import utc_now, write_json

MODES = ("fixed", "cpu-hpa", "lag-keda")


def mode_snapshot(mode):
    objects = json.loads(
        kubectl("get", "deployment,pod,hpa,scaledobject", "-o", "json", capture=True).stdout
    )["items"]
    deployment = next(
        row
        for row in objects
        if row["kind"] == "Deployment" and row["metadata"]["name"] == "streamscale-consumer"
    )
    hpas = [
        row
        for row in objects
        if row["kind"] == "HorizontalPodAutoscaler"
        and row["spec"]["scaleTargetRef"]["name"] == "streamscale-consumer"
    ]
    scaled = [
        row
        for row in objects
        if row["kind"] == "ScaledObject"
        and row["spec"]["scaleTargetRef"]["name"] == "streamscale-consumer"
    ]
    desired = deployment["spec"]["replicas"]
    run_name = deployment["spec"]["template"]["metadata"]["annotations"]["streamscale/run-name"]
    pods = [
        row
        for row in objects
        if row["kind"] == "Pod"
        and row["metadata"].get("labels", {}).get("app") == "streamscale-consumer"
        and row["metadata"].get("annotations", {}).get("streamscale/run-name") == run_name
        and not row["metadata"].get("deletionTimestamp")
    ]
    ready = sum(
        any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in pod["status"].get("conditions", [])
        )
        for pod in pods
    )
    if not 1 <= desired <= (1 if mode == "fixed" else 3) or ready > 3:
        raise RuntimeError("Consumer replica target escaped its mode bounds")
    if mode == "fixed":
        if hpas or scaled:
            raise RuntimeError("Fixed mode has an active autoscaler")
    elif mode == "cpu-hpa":
        if len(hpas) != 1 or scaled or hpas[0]["metadata"]["name"] != "streamscale-cpu":
            raise RuntimeError("CPU-HPA mode is not isolated")
        if hpas[0]["spec"]["metrics"][0]["type"] != "Resource":
            raise RuntimeError("CPU-HPA is not driven by resource metrics")
    else:
        if len(scaled) != 1 or any(
            row["metadata"]["name"] != "keda-hpa-streamscale-consumer" for row in hpas
        ):
            raise RuntimeError("Lag-KEDA mode is not isolated")
        spec = scaled[0]["spec"]
        if spec["minReplicaCount"] != 1 or spec["maxReplicaCount"] != 3:
            raise RuntimeError("KEDA replica bounds differ from 1–3")
    for hpa in hpas:
        if hpa["spec"]["minReplicas"] != 1 or hpa["spec"]["maxReplicas"] != 3:
            raise RuntimeError("HPA replica bounds differ from 1–3")
    return {
        "at": utc_now(),
        "mode": mode,
        "desired": desired,
        "ready": ready,
        "hpas": hpas,
        "scaledobjects": scaled,
        "pods": pods,
    }


def inspect(directory):
    result = kubectl(
        "exec",
        "deployment/streamscale-consumer",
        "--",
        "/app/.venv/bin/streamscale",
        "inspect",
        "--run-dir",
        directory,
        capture=True,
        check=False,
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def exercise(metadata, mode, orders=3000, timeout=300):
    args = SimpleNamespace(
        mode=mode,
        scenario="steady",
        seed=42,
        orders=orders,
        profile="correctness",
        paced=False,
        start_delay=3,
    )
    info = deploy(metadata, args, wait=False)
    folder = OUTPUT / info["run_name"]
    folder.mkdir(exist_ok=True)
    deadline = time.monotonic() + timeout
    maximum = 0
    reconciliation = None
    with (folder / "scaling-samples.jsonl").open("w") as out:
        while time.monotonic() < deadline:
            sample = mode_snapshot(mode)
            maximum = max(maximum, sample["ready"])
            out.write(json.dumps(sample) + "\n")
            out.flush()
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
                raise RuntimeError("Scaling workload failed; inspect its Job logs")
            if job["status"].get("succeeded"):
                reconciliation = inspect("/runs/" + info["run_name"])
                if reconciliation and (mode == "fixed" or maximum > 1):
                    break
            time.sleep(2)
    if reconciliation is None:
        raise RuntimeError(f"{mode} did not reconcile before the scaling deadline")
    if mode != "fixed" and maximum < 2:
        raise RuntimeError(f"{mode} never added a ready consumer under backlog")
    write_json(folder / "inspection.json", reconciliation)
    result = {
        "status": "passed",
        "mode": mode,
        "run_name": info["run_name"],
        "run_id": reconciliation["run_id"],
        "max_ready_replicas": maximum,
        "stored_facts": reconciliation["stored_facts"],
        "replica_bounds": [1, 1 if mode == "fixed" else 3],
        "mode_isolation": True,
        "verified_at": utc_now(),
    }
    write_json(folder / "scaling-verification.json", result)
    print(json.dumps(result), flush=True)
    return result


def main():
    metadata = build()
    cluster()
    call([TOOLS / "kind", "load", "docker-image", metadata["image"], "--name", CLUSTER])
    install_addons()
    results = [exercise(metadata, mode) for mode in MODES]
    write_json(OUTPUT / "scaling-verification.json", {"status": "passed", "modes": results})
    # Leave the standard dashboard demo and one fixed consumer available.
    args = SimpleNamespace(
        mode="fixed",
        scenario="steady",
        seed=42,
        orders=200,
        profile="correctness",
        paced=False,
        start_delay=0,
    )
    deploy(metadata, args)
    from verify import main as verify

    verify()


if __name__ == "__main__":
    main()
