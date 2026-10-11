"""Reconcile a console run against its Kafka source range, SQL and the DLQ.

The measured reconciler (`streamscale inspect`) requires every planned attempt to be
acknowledged, which a stopped, aborted or interrupted sale never satisfies. This one
uses the source log itself as ground truth for what was published:

- every acknowledged valid event ID is stored, and stored once;
- nothing is stored that is not a valid record in the run's source range;
- latest state equals the greatest (event_time, offset) per order in the log;
- every invalid source record has at least one DLQ envelope (copies are counted
  separately, because a crash after DLQ acknowledgement can file one twice);
- the run's consumer group has committed through the end of the range.

It also reports exact SQL end-to-end latency percentiles for the stored facts.
"""

import base64
import json
import math
import threading
import time
from pathlib import Path
from uuid import UUID

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition

from streamscale.contract import ContractError, timestamp, validate_event
from streamscale.run_artifacts import offsets, read_jsonl, read_run, utc_now, write_json
from streamscale.storage import STATUSES
from streamscale.topics import DLQ_TOPIC, INPUT_TOPIC, PARTITIONS

MAX_RECORDS = 500_000


def read_range(bootstrap, topic, starts, ends, deadline_seconds=120):
    """All records in [start, end) per partition, without joining a consumer group."""
    reader = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "streamscale.console.reconcile",
            "enable.auto.commit": False,
            "enable.partition.eof": True,
        }
    )
    pending = {part for part in range(PARTITIONS) if starts[part] < ends[part]}
    records = []
    try:
        if not pending:
            return records
        reader.assign([TopicPartition(topic, part, starts[part]) for part in pending])
        deadline = time.monotonic() + deadline_seconds
        while pending and time.monotonic() < deadline:
            for message in reader.consume(num_messages=1000, timeout=0.5):
                part = message.partition()
                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        if message.offset() >= ends[part]:
                            pending.discard(part)
                        continue
                    raise KafkaException(message.error())
                if message.offset() >= ends[part]:
                    pending.discard(part)
                    continue
                records.append((part, message.offset(), message.key(), message.value()))
                if message.offset() + 1 >= ends[part]:
                    pending.discard(part)
                if len(records) > MAX_RECORDS:
                    raise RuntimeError("Run range exceeds the reconciliation bound")
        if pending:
            raise RuntimeError("Timed out reading the run's source range")
    finally:
        reader.close()
    return records


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[rank]


def acknowledgements(directory):
    path = directory / "acknowledgements.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # A publisher killed mid-write leaves a partial last line; it is unconfirmed.
            continue
    return rows


def reconcile(directory: Path, settings):
    run = read_run(directory)
    if run.get("status") != "published":
        raise RuntimeError("Only a finalized (published) console run can be reconciled")
    started = time.time()
    starts = [run["start_offsets"][str(part)] for part in range(PARTITIONS)]
    ends = [run["end_offsets"][str(part)] for part in range(PARTITIONS)]
    records = read_range(settings.kafka_bootstrap_servers, INPUT_TOPIC, starts, ends)
    valid = {}
    invalid = {}
    foreign = 0
    for part, offset, key, value in records:
        try:
            event = validate_event(value, key)
        except ContractError as error:
            invalid[(part, offset)] = error.code
            continue
        if event["run_id"] != run["run_id"]:
            foreign += 1
            continue
        valid.setdefault(event["event_id"], []).append((part, offset, event))
    acks = acknowledgements(directory)
    acked_coordinates = {(row["partition"], row["offset"]) for row in acks}
    acked_valid_ids = {row["event_id"] for row in acks if row["valid"]}
    in_log = {(part, offset) for part, offset, _, _ in records}
    # Expected state: the first log occurrence of each event, then max (event_time, offset).
    expected = {}
    for _event_id, occurrences in valid.items():
        part, offset, event = min(occurrences, key=lambda row: (row[0], row[1]))
        pair = (timestamp(event["event_time"]), offset)
        order = event["order_id"]
        if order not in expected or pair > expected[order][0]:
            expected[order] = (
                pair,
                event["event_id"],
                part,
                STATUSES[event["event_type"]],
                event["order_amount"],
            )
    with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
        connection.execute("SET statement_timeout = '60s'")
        facts = connection.execute(
            """SELECT event_id::text, kafka_partition, kafka_offset,
                      EXTRACT(EPOCH FROM ingested_at - produced_at)
               FROM order_event WHERE run_id = %s""",
            (UUID(run["run_id"]),),
        ).fetchall()
        state = connection.execute(
            """SELECT order_id::text, last_event_time, last_kafka_offset,
                      last_event_id::text, last_kafka_partition, status, order_amount::text
               FROM order_state WHERE run_id = %s""",
            (UUID(run["run_id"]),),
        ).fetchall()
        ended = connection.execute(
            "SELECT ended_at FROM experiment_run WHERE run_id = %s", (UUID(run["run_id"]),)
        ).fetchone()
    stored = {row[0] for row in facts}
    actual = {row[0]: ((row[1], row[2]), row[3], row[4], row[5], row[6]) for row in state}
    regressions = sum(actual.get(order) != value for order, value in expected.items())
    regressions += len(actual.keys() - expected.keys())
    dlq_end = offsets(settings.kafka_bootstrap_servers, DLQ_TOPIC)
    dlq_starts = [run["dlq_start_offsets"][str(part)] for part in range(PARTITIONS)]
    dlq_records = read_range(
        settings.kafka_bootstrap_servers,
        DLQ_TOPIC,
        dlq_starts,
        [dlq_end[str(part)] for part in range(PARTITIONS)],
    )
    envelopes = {}
    for _part, _offset, _key, value in dlq_records:
        try:
            envelope = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        source = envelope.get("source") or {}
        coordinate = (source.get("partition"), source.get("offset"))
        if coordinate in invalid and source.get("topic") == INPUT_TOPIC:
            envelopes.setdefault(coordinate, []).append(envelope)
    group = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": run["group_id"],
            "enable.auto.commit": False,
        }
    )
    try:
        committed = group.committed(
            [TopicPartition(INPUT_TOPIC, part) for part in range(PARTITIONS)], timeout=10
        )
    finally:
        group.close()
    remaining = {
        str(part.partition): max(0, ends[part.partition] - max(part.offset, starts[part.partition]))
        for part in committed
    }
    latencies = [float(row[3]) for row in facts if row[3] is not None]
    console = run.get("console", {})
    result = {
        "run_id": run["run_id"],
        "run_name": directory.name,
        "reconciled_at": utc_now(),
        "duration_s": round(time.time() - started, 2),
        "outcome": console.get("outcome"),
        "planned_attempts": run["attempts"],
        "acknowledged": len(acks),
        "log_records": len(records),
        "unacknowledged_in_log": len(in_log - acked_coordinates),
        "acknowledged_not_in_log": len(acked_coordinates - in_log),
        "foreign_records": foreign,
        "valid_unique_in_log": len(valid),
        "acknowledged_valid_unique": len(acked_valid_ids),
        "stored_facts": len(facts),
        "missing_acknowledged_ids": sorted(acked_valid_ids - stored)[:50],
        "missing_acknowledged_count": len(acked_valid_ids - stored),
        "unexpected_stored_ids": sorted(stored - set(valid))[:50],
        "duplicate_stored_ids": len(facts) - len(stored),
        "log_duplicates": sum(len(rows) - 1 for rows in valid.values()),
        "state_rows": len(state),
        "state_regressions": regressions,
        "invalid_sources": len(invalid),
        "dlq_copies": sum(len(rows) for rows in envelopes.values()),
        "dlq_unique_sources": len(envelopes),
        "missing_dlq_sources": len(invalid.keys() - envelopes.keys()),
        "remaining_lag": remaining,
        "run_ended_at": ended[0].isoformat() if ended and ended[0] else None,
        "latency_s": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
            "count": len(latencies),
            "source": "SQL ingested_at - produced_at for every stored fact",
        },
    }
    checks = {
        "no_missing_acknowledged_ids": result["missing_acknowledged_count"] == 0,
        "no_unexpected_ids": not result["unexpected_stored_ids"],
        "no_duplicate_stored_ids": result["duplicate_stored_ids"] == 0,
        "no_state_regression": regressions == 0,
        "every_invalid_source_in_dlq": result["missing_dlq_sources"] == 0,
        "no_foreign_records": foreign == 0,
        "acknowledgements_in_log": result["acknowledged_not_in_log"] == 0,
        "backlog_drained": not any(remaining.values()),
    }
    result["checks"] = checks
    result["status"] = "passed" if all(checks.values()) else "failed"
    write_json(directory / "console-reconciliation.json", result)
    return result


class Reconciler:
    """Background reconciliation so long reads never block fault or scaling commands."""

    def __init__(self, agent):
        self.agent = agent
        self.jobs = {}
        self.lock = threading.Lock()
        self.commands = {"reconcile": self.start}
        self.queries = {"reconciliation": self.result}
        self.sections = {"reconciliation": self.section}

    def directory(self, name):
        if (
            not isinstance(name, str)
            or not name.startswith("sale-")
            or not name.replace("-", "").isalnum()
        ):
            raise ValueError("Unknown console run")
        directory = self.agent.root / "runs" / name
        if not (directory / "run.json").exists():
            raise ValueError("Unknown console run")
        return directory

    def start(self, body):
        name = body.get("run_name")
        directory = self.directory(name)
        with self.lock:
            job = self.jobs.get(name)
            if job and job["state"] == "running":
                return {"run_name": name, "state": "running"}
            self.jobs[name] = {"state": "running", "started_at": time.time()}

        def work():
            try:
                result = reconcile(directory, self.agent.settings)
                job = {"state": result["status"], "result": result}
            except Exception as error:  # noqa: BLE001 - reported to the console
                job = {"state": "error", "error": f"{type(error).__name__}: {str(error)[:200]}"}
            with self.lock:
                self.jobs[name] = job | {"ended_at": time.time()}

        threading.Thread(target=work, name=f"reconcile-{name}", daemon=True).start()
        return {"run_name": name, "state": "running"}

    def result(self, parameters):
        name = parameters.get("run")
        directory = self.directory(name)
        with self.lock:
            job = self.jobs.get(name)
        if job is not None:
            return job
        path = directory / "console-reconciliation.json"
        if path.exists():
            result = json.loads(path.read_text())
            return {"state": result["status"], "result": result}
        return {"state": "not-started"}

    def section(self):
        with self.lock:
            return {
                name: {key: value for key, value in job.items() if key != "result"}
                | ({"status": job["result"]["status"]} if "result" in job else {})
                for name, job in list(self.jobs.items())[-5:]
            }


def component(agent):
    return Reconciler(agent)


def decode(value):
    return None if value is None else base64.b64decode(value)


__all__ = ["reconcile", "Reconciler", "read_jsonl"]
