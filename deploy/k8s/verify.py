"""Verify SQL, live scrape targets, run-labelled samples and Grafana provisioning."""

import base64
import json
import time
from contextlib import ExitStack
from urllib.parse import urlencode

from access import fetch, forward
from manage import OUTPUT, ROOT, kubectl

from streamscale.run_artifacts import utc_now, write_json


def query(url, expression):
    result = json.loads(fetch(url + "/api/v1/query?" + urlencode({"query": expression})))
    if result["status"] != "success":
        raise RuntimeError("Prometheus query failed")
    return result["data"]["result"]


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for_run_metrics(prometheus, run_id, expected, fresh_after, timeout=60):
    """Wait for independent consumer/exporter scrapes to reflect SQL reconciliation."""
    deadline = time.monotonic() + timeout
    metrics = (
        *expected,
        "streamscale_kafka_lag",
        "streamscale_failed_total",
        "streamscale_exporter_last_success_unixtime",
    )
    while True:
        samples = {
            metric: query(prometheus, metric + '{run_id="' + run_id + '"}') for metric in metrics
        }
        problems = []
        for metric, rows in samples.items():
            if not rows:
                problems.append(f"missing series: {metric}")
        for metric, value in expected.items():
            observed = sum(float(row["value"][1]) for row in samples[metric])
            if samples[metric] and observed != value:
                problems.append(f"{metric}: expected {value}, observed {observed:g}")
        lag = samples["streamscale_kafka_lag"]
        if {row["metric"].get("partition") for row in lag} != {"0", "1", "2"}:
            problems.append("Kafka lag samples must cover all three partitions")
        pending = sum(float(row["value"][1]) for row in lag)
        if pending != 0:
            problems.append(f"Kafka backlog did not drain: observed lag {pending:g}")
        freshness = samples["streamscale_exporter_last_success_unixtime"]
        if freshness and min(float(row["value"][1]) for row in freshness) < fresh_after:
            problems.append("Kafka exporter sample predates SQL reconciliation")
        check(
            sum(float(row["value"][1]) for row in samples["streamscale_failed_total"]) == 0,
            "The cluster run had handling failures",
        )
        if not problems:
            return samples
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Run metrics did not settle before the deadline: " + "; ".join(problems)
            )
        time.sleep(1)


def main():
    info = json.loads((OUTPUT / "deployment.json").read_text())
    directory = "/runs/" + info["run_name"]
    destination = OUTPUT / info["run_name"]
    destination.mkdir(exist_ok=True)
    deployment = json.loads(
        kubectl("get", "deployment", "streamscale-consumer", "-o", "json", capture=True).stdout
    )
    check(
        deployment["spec"]["replicas"] == 1 and deployment["status"].get("readyReplicas") == 1,
        "Expected exactly one ready consumer",
    )
    check(
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] == info["image"],
        "Deployed consumer image differs from the captured build",
    )
    write_json(destination / "consumer-deployment.json", deployment)
    deadline = time.monotonic() + 60
    while True:
        inspected = kubectl(
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
        if inspected.returncode == 0:
            inspection = json.loads(inspected.stdout)
            reconciled_at = time.time()
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Cluster SQL/DLQ reconciliation did not pass before the deadline")
        time.sleep(2)
    for name in ("run.json", "manifest.jsonl", "acknowledgements.jsonl", "inspection.json"):
        data = kubectl(
            "exec",
            "deployment/streamscale-consumer",
            "--",
            "/bin/cat",
            directory + "/" + name,
            capture=True,
        ).stdout
        (destination / name).write_text(data)
    run = json.loads((destination / "run.json").read_text())
    check(
        run["source_sha256"] == info["source_sha256"] and run["image"] == info["image"],
        "Run/build provenance does not match",
    )
    credential = json.loads((OUTPUT / "credentials.json").read_text())
    authorization = (
        "Basic "
        + base64.b64encode(
            f"{credential['grafana_user']}:{credential['grafana_password']}".encode()
        ).decode()
    )
    with ExitStack() as stack:
        prometheus = stack.enter_context(forward("prometheus"))
        grafana = stack.enter_context(forward("grafana"))
        consumer = stack.enter_context(forward("consumer"))
        exporter = stack.enter_context(forward("exporter"))
        for role, endpoint in (("consumer", consumer), ("exporter", exporter)):
            check(fetch(endpoint + "/health/ready") == b"ready\n", f"{role} readiness failed")
            (destination / f"{role}-metrics.txt").write_bytes(fetch(endpoint + "/metrics"))
        expected = {
            "streamscale_generated_total": run["attempts"],
            "streamscale_published_total": run["acknowledged"],
            "streamscale_accepted_total": inspection["stored_facts"],
            "streamscale_duplicate_total": run["duplicate_attempts"],
            "streamscale_invalid_total": run["invalid_attempts"],
            "streamscale_dlq_total": inspection["dlq_copies"],
            "streamscale_consumer_replicas": 1,
            "streamscale_end_to_end_seconds_count": inspection["stored_facts"],
        }
        samples = wait_for_run_metrics(
            prometheus, run["run_id"], expected, fresh_after=reconciled_at
        )
        for rows in samples.values():
            for row in rows:
                check(
                    row["metric"]["mode"] == "fixed"
                    and row["metric"]["scenario"] == run["scenario"],
                    "Metric mode/scenario labels do not match the run",
                )
                check(
                    not ({"event_id", "order_id"} & row["metric"].keys()), "Unbounded metric label"
                )
        targets = json.loads(fetch(prometheus + "/api/v1/targets"))
        for job in ("consumer", "run-exporter"):
            selected = [
                target
                for target in targets["data"]["activeTargets"]
                if target["labels"]["job"] == job
            ]
            check(
                len(selected) == 1 and selected[0]["health"] == "up", f"Unhealthy scrape job {job}"
            )
        write_json(destination / "prometheus-targets.json", targets)
        dashboard = json.loads(
            fetch(
                grafana
                + "/apis/dashboard.grafana.app/v1/namespaces/default/dashboards/streamscale",
                {"Authorization": authorization},
            )
        )
        check(dashboard["metadata"]["name"] == "streamscale", "Grafana dashboard is missing")
        check(len(dashboard["spec"]["panels"]) == 12, "Grafana dashboard panels are missing")
        health = json.loads(
            fetch(
                grafana + "/api/datasources/uid/streamscale-prometheus/health",
                {"Authorization": authorization},
            )
        )
        check(health["status"] == "OK", "Grafana cannot query its Prometheus data source")
        write_json(destination / "grafana-datasource-health.json", health)
        write_json(destination / "grafana-dashboard.json", dashboard)
        write_json(destination / "prometheus-samples.json", samples)
    result = {
        "status": "passed",
        "verified_at": utc_now(),
        "run_id": run["run_id"],
        "image": info["image"],
        "consumer_replicas": 1,
        "stored_facts": inspection["stored_facts"],
        "state_rows": inspection["state_rows"],
        "dlq_unique_sources": inspection["dlq_unique_sources"],
        "remaining_lag": 0,
        "missing_valid_ids": len(inspection["missing_valid_ids"]),
        "state_regressions": inspection["state_regressions"],
        "artifact_directory": str(destination.relative_to(ROOT)),
    }
    write_json(destination / "verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
