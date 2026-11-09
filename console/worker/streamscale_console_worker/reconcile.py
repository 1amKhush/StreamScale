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
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import UTC
from pathlib import Path
from uuid import UUID

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition

from streamscale.contract import ContractError, timestamp, validate_event
from streamscale.run_artifacts import offsets, read_jsonl, read_run, utc_now, write_json
from streamscale.storage import STATUSES
from streamscale.topics import DLQ_TOPIC, INPUT_TOPIC

MAX_RECORDS = 500_000


def read_range(bootstrap, topic, starts, ends, deadline_seconds=120):
    """All records in [start, end) per partition, without joining a consumer group."""
    reader = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "streamscale.console.reconcile",
            "enable.auto.commit": False,
            "enable.partition.eof": True,
            "queued.max.messages.kbytes": 8192,
            "fetch.max.bytes": 8 * 1024**2,
        }
    )
    pending = {part for part in range(len(starts)) if starts[part] < ends[part]}
    count = 0
    try:
        if not pending:
            return
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
                count += 1
                if count > MAX_RECORDS:
                    raise RuntimeError("Run range exceeds the reconciliation bound")
                yield part, message.offset(), message.key(), message.value()
                if message.offset() + 1 >= ends[part]:
                    pending.discard(part)
        if pending:
            raise RuntimeError("Timed out reading the run's source range")
    finally:
        reader.close()


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[rank]


def acknowledgements(directory):
    path = directory / "acknowledgements.jsonl"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A publisher killed mid-write can leave an unconfirmed partial line.
                continue


def normalized_time(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def reconcile(directory: Path, settings, request_id=None):
    """Stream a run into disposable, disk-backed proof tables, never full payload lists.

    The temporary database is private to this attempt. The durable SQL schema,
    source offsets and prior reconciliation artifact are untouched on failure.
    """
    run = read_run(directory)
    if run.get("status") != "published":
        raise RuntimeError("Only a finalized (published) console run can be reconciled")
    started = time.time()
    partition_count = len(run["start_offsets"])
    starts = [run["start_offsets"][str(part)] for part in range(partition_count)]
    ends = [run["end_offsets"][str(part)] for part in range(partition_count)]
    with tempfile.TemporaryDirectory(prefix="streamscale-reconcile-") as scratch:
        with closing(sqlite3.connect(Path(scratch) / "proof.sqlite")) as proof:
            proof.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA cache_size=-8192;
                PRAGMA temp_store=FILE;
                CREATE TABLE source (part INTEGER, off INTEGER, PRIMARY KEY(part, off));
                CREATE TABLE invalid (part INTEGER, off INTEGER, PRIMARY KEY(part, off));
                CREATE TABLE ack (part INTEGER, off INTEGER, PRIMARY KEY(part, off));
                CREATE TABLE ack_id (id TEXT PRIMARY KEY);
                CREATE TABLE valid (
                    id TEXT PRIMARY KEY, part INTEGER, off INTEGER, order_id TEXT,
                    event_time TEXT, status TEXT, amount TEXT, ordinal INTEGER
                );
                CREATE TABLE fact (id TEXT, latency REAL);
                CREATE TABLE dlq (part INTEGER, off INTEGER, copies INTEGER,
                                  PRIMARY KEY(part, off));
            """)

            def count(query, parameters=()):
                return proof.execute(query, parameters).fetchone()[0]

            def identifiers(query):
                return [row[0] for row in proof.execute(query)]

            source_count = valid_count = foreign = 0
            records = read_range(settings.kafka_bootstrap_servers, INPUT_TOPIC, starts, ends)
            for part, offset, key, value in records:
                source_count += 1
                proof.execute("INSERT OR IGNORE INTO source VALUES (?, ?)", (part, offset))
                try:
                    event = validate_event(value, key)
                except ContractError:
                    proof.execute("INSERT OR IGNORE INTO invalid VALUES (?, ?)", (part, offset))
                    continue
                if event["run_id"] != run["run_id"]:
                    foreign += 1
                    continue
                valid_count += 1
                # Preserve the first source occurrence of an ID, including duplicates
                # encountered out of offset order while reading different partitions.
                proof.execute(
                    """INSERT INTO valid VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         part=excluded.part, off=excluded.off, order_id=excluded.order_id,
                         event_time=excluded.event_time, status=excluded.status,
                         amount=excluded.amount
                       WHERE (excluded.part, excluded.off) < (valid.part, valid.off)""",
                    (
                        event["event_id"],
                        part,
                        offset,
                        event["order_id"],
                        normalized_time(timestamp(event["event_time"])),
                        STATUSES[event["event_type"]],
                        event["order_amount"],
                        source_count,
                    ),
                )
            ack_count = 0
            for row in acknowledgements(directory):
                ack_count += 1
                proof.execute(
                    "INSERT OR IGNORE INTO ack VALUES (?, ?)", (row["partition"], row["offset"])
                )
                if row["valid"]:
                    proof.execute("INSERT OR IGNORE INTO ack_id VALUES (?)", (row["event_id"],))
            proof.executescript("""
                CREATE INDEX valid_order ON valid(order_id, event_time DESC, off DESC, ordinal);
                CREATE TABLE expected AS
                SELECT order_id, event_time, off, id, part, status, amount, 0 AS seen
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY order_id ORDER BY event_time DESC, off DESC, ordinal
                    ) AS position FROM valid
                ) WHERE position=1;
                CREATE UNIQUE INDEX expected_order ON expected(order_id);
            """)
            regressions = state_count = 0
            with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
                connection.execute("SET statement_timeout = '60s'")
                with connection.cursor(name="reconcile_facts") as cursor:
                    cursor.itersize = 1000
                    cursor.execute(
                        """SELECT event_id::text, EXTRACT(EPOCH FROM ingested_at - produced_at)
                           FROM order_event WHERE run_id = %s""",
                        (UUID(run["run_id"]),),
                    )
                    proof.executemany(
                        "INSERT INTO fact VALUES (?, ?)",
                        (
                            (identity, float(latency) if latency is not None else None)
                            for identity, latency in cursor
                        ),
                    )
                with connection.cursor(name="reconcile_state") as cursor:
                    cursor.itersize = 1000
                    cursor.execute(
                        """SELECT order_id::text, last_event_time, last_kafka_offset,
                                  last_event_id::text, last_kafka_partition,
                                  status, order_amount::text
                           FROM order_state WHERE run_id = %s""",
                        (UUID(run["run_id"]),),
                    )
                    for order, when, offset, identity, part, status, amount in cursor:
                        state_count += 1
                        expected = proof.execute(
                            "SELECT event_time, off, id, part, status, amount "
                            "FROM expected WHERE order_id=?",
                            (order,),
                        ).fetchone()
                        actual = (normalized_time(when), offset, identity, part, status, amount)
                        regressions += expected != actual
                        proof.execute("UPDATE expected SET seen=1 WHERE order_id=?", (order,))
                ended = connection.execute(
                    "SELECT ended_at FROM experiment_run WHERE run_id = %s", (UUID(run["run_id"]),)
                ).fetchone()
            regressions += count("SELECT COUNT(*) FROM expected WHERE seen=0")
            proof.executescript(
                "CREATE INDEX fact_id ON fact(id); CREATE INDEX fact_latency ON fact(latency);"
            )
            dlq_end = offsets(settings.kafka_bootstrap_servers, DLQ_TOPIC)
            dlq_starts = [run["dlq_start_offsets"][str(part)] for part in range(partition_count)]
            for _part, _offset, _key, value in read_range(
                settings.kafka_bootstrap_servers,
                DLQ_TOPIC,
                dlq_starts,
                [dlq_end[str(part)] for part in range(partition_count)],
            ):
                try:
                    envelope = json.loads(value)
                    source = envelope.get("source") or {}
                    coordinate = (source.get("partition"), source.get("offset"))
                except (TypeError, json.JSONDecodeError, AttributeError):
                    continue
                if source.get("topic") == INPUT_TOPIC and count(
                    "SELECT COUNT(*) FROM invalid WHERE part=? AND off=?", coordinate
                ):
                    proof.execute(
                        """INSERT INTO dlq VALUES (?, ?, 1) ON CONFLICT(part, off)
                           DO UPDATE SET copies=copies+1""",
                        coordinate,
                    )
            group = Consumer(
                {
                    "bootstrap.servers": settings.kafka_bootstrap_servers,
                    "group.id": run["group_id"],
                    "enable.auto.commit": False,
                }
            )
            try:
                committed = group.committed(
                    [TopicPartition(INPUT_TOPIC, part) for part in range(partition_count)],
                    timeout=10,
                )
            finally:
                group.close()
            remaining = {
                str(part.partition): max(
                    0, ends[part.partition] - max(part.offset, starts[part.partition])
                )
                for part in committed
            }
            latency_count = count("SELECT COUNT(latency) FROM fact")

            def quantile(q):
                if not latency_count:
                    return None
                return count(
                    "SELECT latency FROM fact WHERE latency IS NOT NULL "
                    "ORDER BY latency LIMIT 1 OFFSET ?",
                    (max(0, math.ceil(q * latency_count) - 1),),
                )

            missing = (
                "SELECT id FROM ack_id a WHERE NOT EXISTS (SELECT 1 FROM fact f WHERE f.id=a.id)"
            )
            unexpected = (
                "SELECT DISTINCT id FROM fact f WHERE NOT EXISTS "
                "(SELECT 1 FROM valid v WHERE v.id=f.id)"
            )
            valid_unique = count("SELECT COUNT(*) FROM valid")
            fact_count = count("SELECT COUNT(*) FROM fact")
            result = {
                "request_id": request_id,
                "run_id": run["run_id"],
                "run_name": directory.name,
                "reconciled_at": utc_now(),
                "duration_s": round(time.time() - started, 2),
                "outcome": run.get("console", {}).get("outcome"),
                "planned_attempts": run["attempts"],
                "acknowledged": ack_count,
                "log_records": source_count,
                "unacknowledged_in_log": count(
                    "SELECT COUNT(*) FROM source s WHERE NOT EXISTS "
                    "(SELECT 1 FROM ack a WHERE a.part=s.part AND a.off=s.off)"
                ),
                "acknowledged_not_in_log": count(
                    "SELECT COUNT(*) FROM ack a WHERE NOT EXISTS "
                    "(SELECT 1 FROM source s WHERE a.part=s.part AND a.off=s.off)"
                ),
                "foreign_records": foreign,
                "valid_unique_in_log": valid_unique,
                "acknowledged_valid_unique": count("SELECT COUNT(*) FROM ack_id"),
                "stored_facts": fact_count,
                "missing_acknowledged_ids": identifiers(missing + " ORDER BY id LIMIT 50"),
                "missing_acknowledged_count": count("SELECT COUNT(*) FROM (" + missing + ")"),
                "unexpected_stored_ids": identifiers(unexpected + " ORDER BY id LIMIT 50"),
                "duplicate_stored_ids": fact_count - count("SELECT COUNT(DISTINCT id) FROM fact"),
                "log_duplicates": valid_count - valid_unique,
                "state_rows": state_count,
                "state_regressions": regressions,
                "invalid_sources": count("SELECT COUNT(*) FROM invalid"),
                "dlq_copies": count("SELECT COALESCE(SUM(copies), 0) FROM dlq"),
                "dlq_unique_sources": count("SELECT COUNT(*) FROM dlq"),
                "missing_dlq_sources": count(
                    "SELECT COUNT(*) FROM invalid i WHERE NOT EXISTS "
                    "(SELECT 1 FROM dlq d WHERE d.part=i.part AND d.off=i.off)"
                ),
                "remaining_lag": remaining,
                "run_ended_at": ended[0].isoformat() if ended and ended[0] else None,
                "latency_s": {
                    "p50": quantile(0.50),
                    "p95": quantile(0.95),
                    "p99": quantile(0.99),
                    "max": count("SELECT MAX(latency) FROM fact"),
                    "count": latency_count,
                    "target_seconds": 2,
                    "above_target": count("SELECT COUNT(*) FROM fact WHERE latency>2"),
                    "source": "SQL ingested_at - produced_at for every stored fact",
                },
            }
    checks = {
        "no_missing_acknowledged_ids": result["missing_acknowledged_count"] == 0,
        "no_unexpected_ids": not result["unexpected_stored_ids"],
        "no_duplicate_stored_ids": result["duplicate_stored_ids"] == 0,
        "no_state_regression": result["state_regressions"] == 0,
        "every_invalid_source_in_dlq": result["missing_dlq_sources"] == 0,
        "no_foreign_records": result["foreign_records"] == 0,
        "acknowledgements_in_log": result["acknowledged_not_in_log"] == 0,
        "backlog_drained": not any(result["remaining_lag"].values()),
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
        request_id = body.get("request_id")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 100):
            raise ValueError("Invalid reconciliation request")
        directory = self.directory(name)
        with self.lock:
            job = self.jobs.get(name)
            if job and job["state"] == "running":
                return {"run_name": name, "state": "running"}
            self.jobs[name] = {
                "state": "running",
                "started_at": time.time(),
                "request_id": request_id,
            }

        def work():
            try:
                result = reconcile(directory, self.agent.settings, request_id)
                job = {"state": result["status"], "result": result}
            except Exception as error:  # noqa: BLE001 - reported to the console
                job = {"state": "error", "error": f"{type(error).__name__}: {str(error)[:200]}"}
            with self.lock:
                self.jobs[name] = job | {"ended_at": time.time(), "request_id": request_id}

        threading.Thread(target=work, name=f"reconcile-{name}", daemon=True).start()
        return {"run_name": name, "state": "running"}

    def result(self, parameters):
        name = parameters.get("run")
        requested = parameters.get("request_id")
        directory = self.directory(name)
        with self.lock:
            job = self.jobs.get(name)
        if job is not None:
            if requested is not None and job.get("request_id") != requested:
                return {"state": "not-started"}
            return job
        path = directory / "console-reconciliation.json"
        if path.exists():
            result = json.loads(path.read_text())
            if requested is not None and result.get("request_id") != requested:
                return {"state": "not-started"}
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
