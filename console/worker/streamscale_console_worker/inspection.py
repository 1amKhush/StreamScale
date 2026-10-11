"""Bounded, read-only inspection of stored facts, latest state, DLQ envelopes and acks.

Queries are limited to the current console run (or a named one), use read-only
transactions with a short statement timeout, and return at most a few dozen rows.
"""

import base64
import json
import threading
from collections import Counter, deque
from pathlib import Path
from uuid import UUID

import psycopg
from confluent_kafka import Consumer, KafkaError, TopicPartition

from streamscale.run_artifacts import read_jsonl, read_run
from streamscale.topics import DLQ_TOPIC, PARTITIONS
from streamscale_console_worker import control


class InspectionError(ValueError):
    pass


def _uuid(value, name):
    try:
        return UUID(str(value))
    except ValueError:
        raise InspectionError(f"{name} must be a UUID") from None


class DeadLetterTail:
    """Read new DLQ envelopes of the current run without joining any consumer group."""

    def __init__(self, bootstrap):
        self.bootstrap = bootstrap
        self.consumer = None
        self.run_id = None
        self.recent = deque(maxlen=40)
        self.codes = Counter()
        self.copies = 0
        self.lock = threading.Lock()

    def follow(self, run):
        if run is None or "dlq_start_offsets" not in run:
            return
        if run["run_id"] != self.run_id:
            if self.consumer is not None:
                self.consumer.close()
            self.consumer = Consumer(
                {
                    "bootstrap.servers": self.bootstrap,
                    "group.id": "streamscale.console.dlq-tail",
                    "enable.auto.commit": False,
                    "enable.partition.eof": False,
                }
            )
            self.consumer.assign(
                [
                    TopicPartition(DLQ_TOPIC, part, run["dlq_start_offsets"][str(part)])
                    for part in range(PARTITIONS)
                ]
            )
            with self.lock:
                self.run_id = run["run_id"]
                self.recent.clear()
                self.codes.clear()
                self.copies = 0
        for message in self.consumer.consume(num_messages=500, timeout=0.05):
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError("DLQ tail read failed")
            try:
                envelope = json.loads(message.value())
            except (TypeError, json.JSONDecodeError):
                continue
            row = {
                "dlq_partition": message.partition(),
                "dlq_offset": message.offset(),
                "error_code": envelope.get("error_code"),
                "detail": (envelope.get("detail") or "")[:200],
                "source": envelope.get("source"),
                "failed_at": envelope.get("failed_at"),
            }
            with self.lock:
                self.recent.appendleft(row)
                self.codes[row["error_code"]] += 1
                self.copies += 1

    def summary(self):
        with self.lock:
            return {
                "run_id": self.run_id,
                "copies_read": self.copies,
                "codes": dict(self.codes),
                "recent": list(self.recent)[:12],
            }


class Inspection:
    def __init__(self, agent):
        self.agent = agent
        self.dlq = DeadLetterTail(agent.settings.kafka_bootstrap_servers)
        self.queries = {
            "facts.recent": self.recent_facts,
            "order": self.order,
            "event": self.event,
            "dlq.recent": lambda parameters: self.dlq.summary(),
        }
        self.sections = {"dlq": self.follow}
        self.context = agent.observer.context

    def follow(self):
        self.dlq.follow(self.context.run)
        return {"codes": self.dlq.summary()["codes"], "copies_read": self.dlq.copies}

    def _run(self, parameters):
        name = parameters.get("run")
        if name:
            if not name.replace("-", "").isalnum():
                raise InspectionError("Unknown run")
            directory = control.ROOT / "runs" / name
            if not (directory / "run.json").exists():
                raise InspectionError("Unknown run")
            run = read_run(directory)
            run["run_dir"] = str(directory)
            return run
        run = self.context.run
        if run is None:
            raise InspectionError("No console sale has run yet")
        return run

    def _connect(self):
        connection = psycopg.connect(
            self.agent.settings.database_url, autocommit=True, connect_timeout=3
        )
        connection.execute("SET statement_timeout = '3s'")
        connection.execute("SET default_transaction_read_only = on")
        return connection

    def recent_facts(self, parameters):
        run = self._run(parameters)
        limit = min(int(parameters.get("limit", 12)), 40)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT event_id::text, order_id::text, event_type, event_time, produced_at,
                          ingested_at, kafka_partition, kafka_offset, order_amount::text
                   FROM order_event WHERE run_id = %s
                   ORDER BY ingested_at DESC LIMIT %s""",
                (UUID(run["run_id"]), limit),
            ).fetchall()
        return {"run_id": run["run_id"], "facts": [_fact(row) for row in rows]}

    def order(self, parameters):
        run = self._run(parameters)
        order = _uuid(parameters.get("order_id"), "order_id")
        with self._connect() as connection:
            facts = connection.execute(
                """SELECT event_id::text, order_id::text, event_type, event_time, produced_at,
                          ingested_at, kafka_partition, kafka_offset, order_amount::text
                   FROM order_event WHERE run_id = %s AND order_id = %s
                   ORDER BY event_time, kafka_offset""",
                (UUID(run["run_id"]), order),
            ).fetchall()
            state = connection.execute(
                """SELECT status, order_amount::text, last_event_id::text, last_event_time,
                          last_kafka_partition, last_kafka_offset, updated_at
                   FROM order_state WHERE run_id = %s AND order_id = %s""",
                (UUID(run["run_id"]), order),
            ).fetchone()
        acknowledgements = [
            _ack(row) for row in self._acknowledgements(run) if _order_of(row) == str(order)
        ]
        return {
            "run_id": run["run_id"],
            "order_id": str(order),
            "facts": [_fact(row) for row in facts],
            "state": None
            if state is None
            else {
                "status": state[0],
                "amount": state[1],
                "last_event_id": state[2],
                "last_event_time": state[3].isoformat(),
                "partition": state[4],
                "offset": state[5],
                "updated_at": state[6].isoformat(),
            },
            "acknowledgements": acknowledgements[:20],
            "explanation": explain_order(facts, state, acknowledgements),
        }

    def event(self, parameters):
        run = self._run(parameters)
        identity = _uuid(parameters.get("event_id"), "event_id")
        acknowledgements = [
            _ack(row) for row in self._acknowledgements(run) if row["event_id"] == str(identity)
        ]
        with self._connect() as connection:
            fact = connection.execute(
                """SELECT event_id::text, order_id::text, event_type, event_time, produced_at,
                          ingested_at, kafka_partition, kafka_offset, order_amount::text
                   FROM order_event WHERE event_id = %s AND run_id = %s""",
                (identity, UUID(run["run_id"])),
            ).fetchone()
        dead = [
            row
            for row in self.dlq.summary()["recent"]
            if any(
                row["source"]
                and row["source"].get("partition") == ack["partition"]
                and row["source"].get("offset") == ack["offset"]
                for ack in acknowledgements
            )
        ]
        return {
            "run_id": run["run_id"],
            "event_id": str(identity),
            "acknowledgements": acknowledgements,
            "fact": None if fact is None else _fact(fact),
            "dead_letters": dead,
            "order_id": fact[1] if fact else None,
        }

    def _acknowledgements(self, run):
        path = Path(run["run_dir"]) / "acknowledgements.jsonl"
        if not path.exists():
            return []
        try:
            return read_jsonl(path)
        except json.JSONDecodeError:
            # The publisher may be mid-line; ignore the partial tail.
            lines = path.read_text(encoding="utf-8").splitlines()[:-1]
            return [json.loads(line) for line in lines if line]


def _fact(row):
    latency = (row[5] - row[4]).total_seconds()
    return {
        "event_id": row[0],
        "order_id": row[1],
        "event_type": row[2],
        "event_time": row[3].isoformat(),
        "produced_at": row[4].isoformat(),
        "ingested_at": row[5].isoformat(),
        "partition": row[6],
        "offset": row[7],
        "amount": row[8],
        "latency_s": round(latency, 4),
    }


def _ack(row):
    return {
        key: row.get(key)
        for key in ("attempt_index", "event_id", "valid", "duplicate", "fault", "produced_at")
    } | {"partition": row["partition"], "offset": row["offset"]}


def _order_of(row):
    try:
        return base64.b64decode(row["key_base64"]).decode("ascii")
    except (TypeError, ValueError):
        return None


def explain_order(facts, state, acknowledgements):
    """Plain-language account of duplicates and late arrivals for one order."""
    notes = []
    duplicates = [ack for ack in acknowledgements if ack["duplicate"]]
    if duplicates:
        notes.append(
            f"{len(duplicates)} deliberate duplicate send(s) reached Kafka; "
            f"the ledger stores each event_id once ({len(facts)} fact(s))."
        )
    late = [ack for ack in acknowledgements if ack["fault"] == "late"]
    if late and state is not None:
        notes.append(
            "A late event was published after a newer one. It is kept in the history, "
            "but the latest state follows the greatest (event_time, offset)."
        )
    if state is not None:
        notes.append(f"Latest state: {state[0]} from event {state[2][:8]}….")
    if not facts and acknowledgements:
        notes.append("Acknowledged but not stored yet: it may still be waiting in the queue.")
    return notes


def component(agent):
    return Inspection(agent)
