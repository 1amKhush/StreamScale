"""Bounded, read-only observation of Kafka, SQL, Kubernetes and console consumers.

Every source is sampled independently and reports its own sample time and error.
A failed source is reported as unknown; it is never replaced with zero. Kafka
inspection reads offsets and group descriptions without joining the run's group.
"""

import json
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient
from prometheus_client.parser import text_string_to_metric_families

from streamscale.run_artifacts import read_run
from streamscale.topics import DLQ_TOPIC, INPUT_TOPIC, PARTITIONS
from streamscale_console_worker import control
from streamscale_console_worker.kube import (
    CONSUMER_APP,
    INFRASTRUCTURE,
    cpu_cores,
    memory_bytes,
)

COUNTERS = {
    "streamscale_accepted_total": "accepted",
    "streamscale_duplicate_total": "duplicate",
    "streamscale_invalid_total": "invalid",
    "streamscale_dlq_total": "dlq",
    "streamscale_late_total": "late",
    "streamscale_assignments_total": "assignments",
    "streamscale_assigned_partitions": "owned",
    "process_cpu_seconds_total": "cpu_seconds",
    "process_resident_memory_bytes": "rss_bytes",
    "process_start_time_seconds": "process_start",
}


def safe_error(error):
    """Driver messages can include connection details; report only our own text."""
    if isinstance(error, KafkaException) and error.args:
        code = error.args[0]
        return {"type": "KafkaException", "message": getattr(code, "name", lambda: "")()}
    if type(error) in {RuntimeError, ValueError, TimeoutError} or type(error).__module__.startswith(
        "streamscale"
    ):
        return {"type": type(error).__name__, "message": str(error)[:240]}
    return {"type": type(error).__name__, "message": type(error).__name__}


def measured(name, function, sources):
    started = time.time()
    try:
        value = function()
    except Exception as error:  # noqa: BLE001 - every source is isolated and reported
        sources[name] = {
            "ok": False,
            "at": started,
            "error": safe_error(error),
            "duration_ms": round((time.time() - started) * 1000, 1),
        }
        return None
    sources[name] = {
        "ok": True,
        "at": started,
        "error": None,
        "duration_ms": round((time.time() - started) * 1000, 1),
    }
    return value


class RunContext:
    """The console run named by the shared pointer, reread only when it changes."""

    def __init__(self, root=control.ROOT):
        self.root = root
        self.signature = None
        self.run = None

    def load(self):
        pointer = control.current_run(self.root)
        if pointer is None:
            self.signature, self.run = None, None
            return None
        path = control.current_path(self.root).parent / "runs" / pointer["run_name"] / "run.json"
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        signature = (pointer["run_name"], stat.st_mtime_ns, stat.st_size)
        if signature != self.signature:
            run = read_run(path.parent)
            run["run_name"] = pointer["run_name"]
            run["run_dir"] = str(path.parent)
            self.signature, self.run = signature, run
        return self.run


class KafkaProbe:
    def __init__(self, bootstrap):
        self.bootstrap = bootstrap
        self.admin = AdminClient({"bootstrap.servers": bootstrap, "socket.timeout.ms": 3000})
        self.offsets = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": "streamscale.console.observer",
                "enable.auto.commit": False,
                "socket.timeout.ms": 3000,
            }
        )
        self.group_probe = None
        self.group_id = None

    def close(self):
        for client in (self.offsets, self.group_probe):
            if client is not None:
                client.close()

    def watermarks(self, topic):
        return [
            self.offsets.get_watermark_offsets(TopicPartition(topic, part), timeout=2, cached=False)
            for part in range(PARTITIONS)
        ]

    def committed(self, group_id):
        if group_id != self.group_id:
            if self.group_probe is not None:
                self.group_probe.close()
            # Never subscribes: committed() is an offset fetch, not group membership.
            self.group_probe = Consumer(
                {
                    "bootstrap.servers": self.bootstrap,
                    "group.id": group_id,
                    "enable.auto.commit": False,
                    "socket.timeout.ms": 3000,
                }
            )
            self.group_id = group_id
        parts = [TopicPartition(INPUT_TOPIC, part) for part in range(PARTITIONS)]
        return [part.offset for part in self.group_probe.committed(parts, timeout=3)]

    def group(self, group_id):
        future = self.admin.describe_consumer_groups([group_id], request_timeout=3)[group_id]
        try:
            description = future.result(timeout=4)
        except KafkaException as error:
            if error.args[0].code() == KafkaError.GROUP_ID_NOT_FOUND:
                return {"id": group_id, "state": "UNKNOWN", "members": []}
            raise
        members = []
        for member in description.members:
            assignment = member.assignment.topic_partitions if member.assignment else []
            members.append(
                {
                    "member_id": member.member_id,
                    "client_id": member.client_id,
                    "host": (member.host or "").lstrip("/"),
                    "partitions": sorted(
                        part.partition for part in assignment if part.topic == INPUT_TOPIC
                    ),
                }
            )
        members.sort(key=lambda row: (row["partitions"] or [99], row["host"]))
        state = getattr(description.state, "name", str(description.state))
        return {"id": group_id, "state": state, "members": members}

    def sample(self, run):
        marks = self.watermarks(INPUT_TOPIC)
        dlq = self.watermarks(DLQ_TOPIC)
        committed = self.committed(run["group_id"]) if run else [None] * PARTITIONS
        group = self.group(run["group_id"]) if run else None
        partitions = []
        for part, ((low, high), saved) in enumerate(zip(marks, committed, strict=True)):
            row = {"partition": part, "low": low, "high": high, "committed": None, "lag": None}
            if run and "start_offsets" in run:
                start = run["start_offsets"][str(part)]
                position = saved if saved is not None and saved >= 0 else max(low, start)
                end = run.get("end_offsets", {}).get(str(part))
                row |= {
                    "committed": saved if saved is not None and saved >= 0 else None,
                    "position": position,
                    "run_start": start,
                    "run_end": end,
                    "run_published": high - start,
                    "lag": max(0, high - position),
                    "run_remaining": None if end is None else max(0, end - position),
                }
            partitions.append(row)
        result = {"topic": INPUT_TOPIC, "partitions": partitions, "group": group}
        if run and "dlq_start_offsets" in run:
            result["dlq"] = {
                "copies": sum(
                    high - run["dlq_start_offsets"][str(part)] for part, (_, high) in enumerate(dlq)
                ),
                "high": [high for _, high in dlq],
            }
        return result


class SqlProbe:
    def __init__(self, database_url):
        self.database_url = database_url
        self.connection = None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connect(self):
        if self.connection is None or self.connection.closed:
            self.connection = psycopg.connect(self.database_url, autocommit=True, connect_timeout=3)
            self.connection.execute("SET statement_timeout = '2s'")
            self.connection.execute("SET default_transaction_read_only = on")
        return self.connection

    def sample(self, run):
        try:
            connection = self._connect()
            started = time.perf_counter()
            connection.execute("SELECT 1").fetchone()
            probe_ms = (time.perf_counter() - started) * 1000
            result = {"probe_ms": round(probe_ms, 2)}
            if run:
                identity = UUID(run["run_id"])
                result["facts"] = connection.execute(
                    "SELECT count(*) FROM order_event WHERE run_id = %s", (identity,)
                ).fetchone()[0]
                rows = connection.execute(
                    "SELECT status, count(*) FROM order_state WHERE run_id = %s GROUP BY status",
                    (identity,),
                ).fetchall()
                result["status_counts"] = {status: count for status, count in rows}
                result["states"] = sum(result["status_counts"].values())
            return result
        except psycopg.Error:
            self.close()
            raise


def pod_summary(pod):
    status = pod.get("status", {})
    containers = status.get("containerStatuses") or [{}]
    main = containers[0]
    spec = pod["spec"]["containers"][0].get("resources", {})
    actual = main.get("resources") or spec
    state = main.get("state", {})
    last = main.get("lastState", {}).get("terminated")
    conditions = {row["type"]: row for row in status.get("conditions", [])}
    resize = [
        name
        for name in ("PodResizePending", "PodResizeInProgress")
        if conditions.get(name, {}).get("status") == "True"
    ]
    return {
        "name": pod["metadata"]["name"],
        "ip": status.get("podIP"),
        "phase": status.get("phase"),
        "ready": conditions.get("Ready", {}).get("status") == "True",
        "restarts": main.get("restartCount", 0),
        "deleting": bool(pod["metadata"].get("deletionTimestamp")),
        "created_at": pod["metadata"].get("creationTimestamp"),
        "started_at": state.get("running", {}).get("startedAt"),
        "waiting_reason": state.get("waiting", {}).get("reason"),
        "terminated_now": state.get("terminated", {}).get("reason"),
        "last_termination": None
        if not last
        else {
            "reason": last.get("reason"),
            "exit_code": last.get("exitCode"),
            "finished_at": last.get("finishedAt"),
        },
        "cpu_limit": actual.get("limits", {}).get("cpu"),
        "cpu_request": actual.get("requests", {}).get("cpu"),
        "memory_limit": actual.get("limits", {}).get("memory"),
        "resize": resize,
        "run_name": pod["metadata"].get("annotations", {}).get("streamscale/run-name"),
    }


def deployment_summary(deployment):
    status = deployment.get("status", {})
    return {
        "desired": deployment["spec"].get("replicas", 0),
        "ready": status.get("readyReplicas", 0),
        "available": status.get("availableReplicas", 0),
        "updated": status.get("updatedReplicas", 0),
        "observed_generation": status.get("observedGeneration"),
        "generation": deployment["metadata"].get("generation"),
    }


def autoscaler_summary(hpas, scaled):
    rows = []
    for hpa in hpas:
        status = hpa.get("status", {})
        current = None
        for metric in status.get("currentMetrics") or []:
            if metric.get("type") == "Resource":
                current = metric["resource"]["current"].get("averageUtilization")
        target = None
        external = None
        for metric in hpa["spec"].get("metrics", []):
            if metric["type"] == "Resource":
                target = metric["resource"]["target"].get("averageUtilization")
            elif metric["type"] == "External":
                external = metric["external"]["target"].get("averageValue")
        rows.append(
            {
                "kind": "HorizontalPodAutoscaler",
                "name": hpa["metadata"]["name"],
                "min": hpa["spec"].get("minReplicas"),
                "max": hpa["spec"].get("maxReplicas"),
                "current_replicas": status.get("currentReplicas"),
                "desired_replicas": status.get("desiredReplicas"),
                "cpu_utilization": current,
                "cpu_target": target,
                "external_target": external,
                "last_scale_time": status.get("lastScaleTime"),
                "conditions": [
                    {
                        "type": row["type"],
                        "status": row["status"],
                        "reason": row.get("reason"),
                        "message": (row.get("message") or "")[:200],
                    }
                    for row in status.get("conditions", [])
                ],
                "owned_by_keda": hpa["metadata"]["name"].startswith("keda-hpa-"),
            }
        )
    for item in scaled:
        trigger = item["spec"]["triggers"][0]["metadata"]
        conditions = {row["type"]: row for row in item.get("status", {}).get("conditions", [])}
        rows.append(
            {
                "kind": "ScaledObject",
                "name": item["metadata"]["name"],
                "min": item["spec"].get("minReplicaCount"),
                "max": item["spec"].get("maxReplicaCount"),
                "group": trigger.get("consumerGroup"),
                "lag_threshold": trigger.get("lagThreshold"),
                "ready": conditions.get("Ready", {}).get("status") == "True",
                "active": conditions.get("Active", {}).get("status") == "True",
                "paused": conditions.get("Paused", {}).get("status") == "True",
                "deleting": bool(item["metadata"].get("deletionTimestamp")),
            }
        )
    return rows


def parse_metrics(text):
    values = {name: None for name in COUNTERS.values()}
    failed = {}
    histograms = {"e2e": {}, "db": {}}
    totals = {"e2e": [0.0, 0.0], "db": [0.0, 0.0]}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            name, labels, value = sample.name, sample.labels, sample.value
            if name in COUNTERS:
                values[COUNTERS[name]] = value
            elif name == "streamscale_failed_total":
                failed[labels["stage"]] = value
            elif name.startswith(("streamscale_end_to_end_seconds", "streamscale_db_seconds")):
                kind = "e2e" if "end_to_end" in name else "db"
                # DB timings include retries; successful handling excludes failed attempts.
                if kind == "db" and labels.get("result") == "failed":
                    continue
                if name.endswith("_bucket"):
                    bound = float(labels["le"])
                    histograms[kind][bound] = histograms[kind].get(bound, 0.0) + value
                elif name.endswith("_sum"):
                    totals[kind][0] += value
                elif name.endswith("_count"):
                    totals[kind][1] += value
    values["failed"] = failed
    for kind in ("e2e", "db"):
        values[kind] = {
            "buckets": sorted(histograms[kind].items()),
            "sum": totals[kind][0],
            "count": totals[kind][1],
        }
    return values


def fetch(url, timeout=0.8):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode()


def scrape_consumer(pod):
    row = {"pod": pod["name"], "ip": pod["ip"], "at": time.time()}
    try:
        row["metrics"] = parse_metrics(fetch(f"http://{pod['ip']}:9100/metrics"))
        row["state"] = json.loads(fetch(f"http://{pod['ip']}:9101/state"))
        row["ok"] = True
    except Exception as error:  # noqa: BLE001 - one pod's failure must not hide others
        row["ok"] = False
        row["error"] = safe_error(error)
    return row


def resource_summary(items):
    result = {}
    for item in items:
        result[item["metadata"]["name"]] = {
            "cpu_cores": sum(cpu_cores(row["usage"]["cpu"]) for row in item["containers"]),
            "memory_bytes": sum(memory_bytes(row["usage"]["memory"]) for row in item["containers"]),
            "window": item.get("window"),
            "timestamp": item.get("timestamp"),
        }
    return result


class Observer:
    """Collect one telemetry sample per interval and retain a bounded history."""

    def __init__(self, settings, kube, root=control.ROOT, extras=None):
        self.settings = settings
        self.kube = kube
        self.root = root
        self.context = RunContext(root)
        self.kafka = None
        self.sql = SqlProbe(settings.database_url)
        self.extras = extras or {}
        self.history = deque(maxlen=1200)
        self.sequence = 0
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.wake = threading.Event()
        self.slow = {}
        self.slow_at = 0.0
        self.pool = ThreadPoolExecutor(max_workers=4)

    def touch(self):
        idle = not self.active()
        self.last_request = time.monotonic()
        if idle:
            # A viewer arrived while sampling slowly: switch to 1 Hz immediately.
            self.wake.set()

    def active(self):
        busy = any(probe() for probe in self.extras.get("busy", []))
        return busy or time.monotonic() - self.last_request < 20

    def kafka_probe(self):
        if self.kafka is None:
            self.kafka = KafkaProbe(self.settings.kafka_bootstrap_servers)
        return self.kafka

    def sample(self):
        sources = {}
        sampled_at = time.time()
        run = measured("run", self.context.load, sources)
        kafka = measured("kafka", lambda: self.kafka_probe().sample(run), sources)
        if not sources["kafka"]["ok"] and self.kafka is not None:
            # Recreate clients after broker loss so a recovered broker is reprobed.
            self.kafka.close()
            self.kafka = None
        sql = measured("sql", lambda: self.sql.sample(run), sources)
        deployment = measured(
            "kubernetes", lambda: deployment_summary(self.kube.consumer_deployment()), sources
        )
        pods = measured(
            "pods", lambda: [pod_summary(pod) for pod in self.kube.console_pods()], sources
        )
        controllers = measured(
            "controllers", lambda: autoscaler_summary(*self.kube.autoscalers()), sources
        )
        infrastructure = measured("infrastructure", self.infrastructure, sources)
        if time.monotonic() - self.slow_at >= 5:
            self.slow_at = time.monotonic()
            self.slow["resources"] = measured(
                "resources", lambda: resource_summary(self.kube.pod_metrics()), sources
            )
            self.slow["resources_source"] = sources["resources"]
        else:
            sources["resources"] = self.slow.get("resources_source")
        consumers = []
        if pods:
            live = [pod for pod in pods if pod["ip"] and pod["phase"] == "Running"]
            consumers = list(self.pool.map(scrape_consumer, live))
        sources["consumers"] = {
            "ok": all(row["ok"] for row in consumers),
            "at": sampled_at,
            "error": None,
            "duration_ms": None,
        }
        sample = {
            "sampled_at": sampled_at,
            "sources": sources,
            "run": summarize_run(run),
            "kafka": kafka,
            "sql": sql,
            "kubernetes": {
                "consumer": deployment,
                "pods": pods,
                "controllers": controllers,
                "infrastructure": infrastructure,
                "resources": self.slow.get("resources"),
            },
            "consumers": consumers,
        }
        for name, provider in self.extras.get("sections", {}).items():
            sample[name] = measured(name, provider, sources)
        with self.lock:
            self.sequence += 1
            sample["sequence"] = self.sequence
            self.history.append(sample)
        return sample

    def infrastructure(self):
        pods = {
            pod["metadata"]["name"]: pod_summary(pod) for pod in self.kube.infrastructure_pods()
        }
        result = {}
        for role in INFRASTRUCTURE:
            statefulset = self.kube.statefulset(role)
            result[role] = {
                "desired": statefulset["spec"].get("replicas", 0),
                "ready": statefulset.get("status", {}).get("readyReplicas", 0),
                "pod": pods.get(INFRASTRUCTURE[role] + "-0"),
            }
        return result

    def latest(self):
        with self.lock:
            return self.history[-1] if self.history else None

    def since(self, sequence, limit=900):
        with self.lock:
            rows = [row for row in self.history if row["sequence"] > sequence]
        return rows[-limit:]

    def run_forever(self, stop):
        while not stop.is_set():
            started = time.monotonic()
            self.sample()
            interval = 1.0 if self.active() else 10.0
            self.wake.wait(max(0.05, interval - (time.monotonic() - started)))
            self.wake.clear()


def summarize_run(run):
    if not run:
        return None
    keys = (
        "run_name",
        "run_id",
        "group_id",
        "status",
        "mode",
        "scenario",
        "seed",
        "attempts",
        "acknowledged",
        "valid_unique",
        "invalid_attempts",
        "duplicate_attempts",
        "publish_started_at",
        "publish_ended_at",
        "start_offsets",
        "end_offsets",
        "console",
    )
    return {key: run.get(key) for key in keys}


__all__ = ["Observer", "CONSUMER_APP"]
