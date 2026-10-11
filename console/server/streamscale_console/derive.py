"""Turn raw agent samples into chart points, with explicit unknowns and reset handling.

Counters and histograms are per consumer process. A process restart resets them,
so increments are accumulated per pod and a decrease is treated as a reset. Live
p95 values are estimates from histogram buckets over a short window; the exact
p95 comes from committed SQL timings after a sale completes.
"""

import math
from collections import deque

COUNTERS = ("accepted", "duplicate", "invalid", "dlq", "late")
LATENCY_WINDOW = 15.0
RATE_WINDOW = 3.0


def quantile(q, buckets):
    """Prometheus-style histogram_quantile over cumulative (upper_bound, count) pairs."""
    if not buckets:
        return None
    buckets = sorted(buckets)
    total = buckets[-1][1]
    if total <= 0:
        return None
    rank = q * total
    previous_bound, previous_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= rank:
            if math.isinf(bound):
                return previous_bound
            if count == previous_count:
                return bound
            return previous_bound + (bound - previous_bound) * (rank - previous_count) / (
                count - previous_count
            )
        previous_bound, previous_count = bound, count
    return buckets[-1][0]


def cores(quantity):
    if quantity is None:
        return None
    text = str(quantity)
    if text.endswith("m"):
        return float(text[:-1]) / 1000
    return float(text)


class Accumulator:
    """Monotonic totals across consumer processes, tolerant of restarts and scale-down."""

    def __init__(self):
        self.run_id = None
        self.last = {}
        self.totals = {name: 0.0 for name in COUNTERS}
        self.failed = {}
        self.histograms = {"e2e": {}, "db": {}}

    def reset(self, run_id):
        self.__init__()
        self.run_id = run_id

    def add(self, consumers, run_id):
        if run_id != self.run_id:
            self.reset(run_id)
        for row in consumers:
            if not row.get("ok"):
                continue
            state = row.get("state") or {}
            if state.get("run_id") != run_id:
                # A process still on an older run (or idle) contributes nothing to this run.
                continue
            metrics = row["metrics"]
            key = row["pod"]
            previous = self.last.get(key)
            if previous and previous.get("process_start") != metrics.get("process_start"):
                previous = None
            for name in COUNTERS:
                self.totals[name] += increment(metrics.get(name), previous and previous.get(name))
            for stage, value in (metrics.get("failed") or {}).items():
                before = previous and (previous.get("failed") or {}).get(stage)
                self.failed[stage] = self.failed.get(stage, 0.0) + increment(value, before)
            for kind in ("e2e", "db"):
                current = dict((bound, count) for bound, count in metrics[kind]["buckets"])
                before = dict(previous[kind]["buckets"]) if previous else {}
                if any(count < before.get(bound, 0) for bound, count in current.items()):
                    before = {}
                target = self.histograms[kind]
                for bound, count in current.items():
                    target[bound] = target.get(bound, 0.0) + count - before.get(bound, 0)
            self.last[key] = metrics

    def snapshot(self):
        return {
            "totals": dict(self.totals),
            "failed": dict(self.failed),
            "histograms": {
                kind: sorted(values.items()) for kind, values in self.histograms.items()
            },
        }


def increment(current, previous):
    if current is None:
        return 0.0
    if previous is None or current < previous:
        return float(current)
    return float(current - previous)


def window_delta(history, now, window, kind):
    """Histogram increments over the window ending now, from accumulated snapshots."""
    if not history:
        return None
    latest = history[-1][1]["histograms"][kind]
    base = None
    for at, snapshot in history:
        if at >= now - window:
            break
        base = snapshot["histograms"][kind]
    base = dict(base or [])
    return [(bound, count - base.get(bound, 0.0)) for bound, count in latest]


class Deriver:
    def __init__(self):
        self.previous = None
        self.accumulator = Accumulator()
        self.history = deque(maxlen=120)
        self.cpu_last = {}

    def point(self, sample, received_at):
        previous = self.previous
        run = sample.get("run") or {}
        run_id = run.get("run_id")
        consumers = sample.get("consumers") or []
        self.accumulator.add(consumers, run_id)
        accumulated = self.accumulator.snapshot()
        at = sample["sampled_at"]
        self.history.append((at, accumulated))
        sources = {
            name: {
                "ok": bool(value and value.get("ok")),
                "at": value.get("at") if value else None,
                "error": (value or {}).get("error"),
            }
            for name, value in (sample.get("sources") or {}).items()
        }
        kafka = sample.get("kafka") or {}
        partitions = kafka.get("partitions") or []
        lag = [row.get("lag") for row in partitions]
        known_lag = len(lag) == 3 and all(value is not None for value in lag)
        group = kafka.get("group") or {}
        members = group.get("members")
        kube = sample.get("kubernetes") or {}
        deployment = kube.get("consumer") or {}
        pods = kube.get("pods") or []
        sale = sample.get("sale") or {}
        sql = sample.get("sql") or {}
        dt = None if previous is None else at - previous["sampled_at"]
        rates = {
            "arrival": None,
            "drain": None,
            "acknowledged": None,
            "accepted_sql": None,
            "processed": None,
            "arrival_by_partition": None,
            "drain_by_partition": None,
            "dead_letters": None,
        }
        if previous and dt and dt > 0:
            old_parts = (previous.get("kafka") or {}).get("partitions") or []
            if len(old_parts) == 3 and len(partitions) == 3:
                rates["arrival"] = (
                    sum(
                        row["high"] - old["high"]
                        for row, old in zip(partitions, old_parts, strict=True)
                    )
                    / dt
                )
                if all(
                    row.get("position") is not None and old.get("position") is not None
                    for row, old in zip(partitions, old_parts, strict=True)
                ):
                    rates["drain"] = max(
                        0.0,
                        sum(
                            row["position"] - old["position"]
                            for row, old in zip(partitions, old_parts, strict=True)
                        )
                        / dt,
                    )
            old_sale = previous.get("sale") or {}
            if sale.get("acknowledged") is not None and old_sale.get("acknowledged") is not None:
                if sale.get("run_id") == old_sale.get("run_id"):
                    rates["acknowledged"] = (sale["acknowledged"] - old_sale["acknowledged"]) / dt
            old_sql = previous.get("sql") or {}
            if sql.get("facts") is not None and old_sql.get("facts") is not None:
                if run_id and run_id == ((previous.get("run") or {}).get("run_id")):
                    rates["accepted_sql"] = max(0.0, (sql["facts"] - old_sql["facts"]) / dt)
        rates["processed"] = self.processed_rate(at)
        rates["dead_letters"] = None
        if previous and dt and dt > 0:
            old_dlq = ((previous.get("kafka") or {}).get("dlq") or {}).get("copies")
            new_dlq = (kafka.get("dlq") or {}).get("copies")
            if old_dlq is not None and new_dlq is not None and new_dlq >= old_dlq:
                rates["dead_letters"] = (new_dlq - old_dlq) / dt
        e2e = window_delta(self.history, at, LATENCY_WINDOW, "e2e")
        db = window_delta(self.history, at, LATENCY_WINDOW, "db")
        pod_rows = []
        for pod in pods:
            scraped = next((row for row in consumers if row["pod"] == pod["name"]), None)
            pod_rows.append(self.pod_point(pod, scraped, at))
        restarts = sum(pod.get("restarts", 0) for pod in pods)
        point = {
            "t": at,
            "received_at": received_at,
            "sequence": sample.get("sequence"),
            "run_id": run_id,
            "sources": sources,
            "rates": rates,
            "lag": lag if known_lag else None,
            "lag_total": sum(lag) if known_lag else None,
            "members": None if members is None else len(members),
            "group_state": group.get("state"),
            "desired": deployment.get("desired"),
            "ready": deployment.get("ready"),
            "pods": pod_rows,
            "facts": sql.get("facts"),
            "states": sql.get("states"),
            "db_probe_ms": sql.get("probe_ms"),
            "e2e_p95": quantile(0.95, e2e) if e2e else None,
            "db_p95": quantile(0.95, db) if db else None,
            "totals": accumulated["totals"],
            "failed": accumulated["failed"],
            "dlq_copies": (kafka.get("dlq") or {}).get("copies"),
            "restarts": restarts,
            "sale": None
            if not sale
            else {
                key: sale.get(key)
                for key in (
                    "state",
                    "run_name",
                    "attempted",
                    "acknowledged",
                    "failed",
                    "budget",
                    "target_rate",
                    "rate_mode",
                    "outcome",
                    "buffer_full",
                )
            },
            "mode": (sample.get("scaling") or {}).get("mode"),
            "override": (sample.get("scaling") or {}).get("override"),
        }
        point["drain_estimate_s"] = drain_estimate(point)
        point["signals"] = signals(point, sample)
        self.previous = sample
        return point

    def processed_rate(self, now):
        if len(self.history) < 2:
            return None
        latest_at, latest = self.history[-1]
        older = [(at, snapshot) for at, snapshot in self.history if at <= now - RATE_WINDOW]
        base_at, base = older[-1] if older else self.history[0]
        if latest_at <= base_at:
            return None
        processed = sum(latest["totals"][name] - base["totals"][name] for name in COUNTERS[:3])
        return max(0.0, processed / (latest_at - base_at))

    def pod_point(self, pod, scraped, at):
        metrics = (scraped or {}).get("metrics") or {}
        state = (scraped or {}).get("state") or {}
        cpu_pct = cpu_request_pct = cpu_used = None
        seconds = metrics.get("cpu_seconds")
        previous = self.cpu_last.get(pod["name"])
        limit = cores(pod.get("cpu_limit"))
        request = cores(pod.get("cpu_request"))
        if seconds is not None and previous and previous[2] == metrics.get("process_start"):
            elapsed = at - previous[0]
            if elapsed > 0:
                cpu_used = max(0.0, (seconds - previous[1]) / elapsed)
                # Limit basis shows headroom; request basis is what the CPU HPA compares.
                cpu_pct = cpu_used / limit * 100 if limit else None
                cpu_request_pct = cpu_used / request * 100 if request else None
        if seconds is not None:
            self.cpu_last[pod["name"]] = (at, seconds, metrics.get("process_start"))
        return {
            "name": pod["name"],
            "ready": pod.get("ready"),
            "phase": pod.get("phase"),
            "deleting": pod.get("deleting"),
            "restarts": pod.get("restarts", 0),
            "cpu_pct": cpu_pct,
            "cpu_request_pct": cpu_request_pct,
            "cpu_cores": cpu_used,
            "cpu_limit": pod.get("cpu_limit"),
            "cpu_request": pod.get("cpu_request"),
            "ip": pod.get("ip"),
            "waiting_reason": pod.get("waiting_reason"),
            "last_termination": pod.get("last_termination"),
            "active_faults": state.get("active_faults"),
            "run_id": state.get("run_id"),
            "rss_bytes": metrics.get("rss_bytes"),
            "state": state.get("state"),
            "assigned": state.get("assigned"),
            "loop_restarts": state.get("loop_restarts"),
            "last_error": state.get("last_error"),
            "scraped": bool(scraped and scraped.get("ok")),
        }


def drain_estimate(point):
    """Seconds to clear the backlog at the current net rate; None when not draining."""
    rates = point["rates"]
    if point["lag_total"] is None or rates["drain"] is None or rates["arrival"] is None:
        return None
    if point["lag_total"] == 0:
        return 0.0
    net = rates["drain"] - rates["arrival"]
    if net <= 0.5:
        return None
    return point["lag_total"] / net


def signals(point, sample):
    """Plain-language observations with their evidence; never inferred from missing data."""
    result = []
    rates = point["rates"]
    lag_total = point["lag_total"]
    infrastructure = (sample.get("kubernetes") or {}).get("infrastructure") or {}
    for role, label in (("kafka", "Kafka broker"), ("postgres", "PostgreSQL")):
        row = infrastructure.get(role)
        if row and (row.get("desired") == 0 or not row.get("ready")):
            result.append(
                {
                    "id": f"{role}-unavailable",
                    "severity": "critical",
                    "component": role,
                    "text": f"{label} is unavailable",
                    "evidence": f"desired {row.get('desired')}, ready {row.get('ready')}",
                }
            )
    if point["members"] == 0 and lag_total:
        result.append(
            {
                "id": "no-consumers",
                "severity": "critical",
                "component": "consumers",
                "text": "No consumer owns a partition, so the backlog cannot drain",
                "evidence": f"0 group members, lag {lag_total}",
            }
        )
    if (
        rates["arrival"] is not None
        and rates["drain"] is not None
        and lag_total is not None
        and lag_total > 50
        and rates["arrival"] > rates["drain"] + 5
    ):
        result.append(
            {
                "id": "backlog-growing",
                "severity": "warning",
                "component": "partitions",
                "text": "Events arrive faster than consumers commit them",
                "evidence": f"arrival {rates['arrival']:.0f}/s, drain {rates['drain']:.0f}/s",
            }
        )
    busy = [
        row
        for row in point["pods"]
        if row["cpu_pct"] is not None and row["cpu_pct"] >= 85 and not row["deleting"]
    ]
    if busy and lag_total:
        result.append(
            {
                "id": "consumers-saturated",
                "severity": "warning",
                "component": "consumers",
                "text": "Consumers are near their CPU limit",
                "evidence": ", ".join(f"{row['name'][-5:]} {row['cpu_pct']:.0f}%" for row in busy),
            }
        )
    if point["db_p95"] is not None and point["db_p95"] >= 0.25 and lag_total:
        result.append(
            {
                "id": "database-slow",
                "severity": "warning",
                "component": "database",
                "text": "Database handling is slow; more consumers may not help",
                "evidence": f"DB p95 {point['db_p95'] * 1000:.0f} ms",
            }
        )
    for row in point["pods"]:
        error = row.get("last_error")
        if row["state"] == "retrying" and error:
            result.append(
                {
                    "id": f"retrying-{row['name']}",
                    "severity": "warning",
                    "component": "consumers",
                    "text": f"{row['name']} is retrying after {error['type']}",
                    "evidence": error.get("message") or error["type"],
                }
            )
    return result
