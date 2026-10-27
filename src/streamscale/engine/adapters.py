"""Kafka input and read-only PostgreSQL output; the original demo is an adapter."""

import os
import time
from uuid import UUID

import psycopg
from confluent_kafka import Producer
from psycopg.rows import dict_row


class KafkaSQL:
    def __init__(self, options):
        self.options = options
        self.producer = None
        self.dsn = os.environ[options.get("database_env", "DATABASE_URL")]
        self.bootstrap = os.environ[options.get("kafka_env", "KAFKA_BOOTSTRAP_SERVERS")]
        for key in ("topic", "query"):
            if not isinstance(options.get(key), str) or not options[key]:
                raise ValueError(f"KafkaSQL needs {key}")

    def prepare(self, context):
        # Connection credentials stay in environment, never in scenario/artifact files.
        self.producer = Producer(
            {
                "bootstrap.servers": self.bootstrap,
                "enable.idempotence": True,
                "acks": "all",
                "delivery.timeout.ms": 10000,
            }
        )
        self.observe(context)  # Fail before publishing if the output query cannot run.

    def send(self, record, acknowledge):
        import json

        def delivery(error, message):
            acknowledge(
                {
                    "id": record["id"],
                    "error": type(error).__name__ if error else None,
                    "topic": message.topic(),
                    "partition": message.partition(),
                    "offset": message.offset(),
                }
            )

        self.producer.produce(
            self.options["topic"],
            key=record["id"].encode(),
            value=json.dumps(record, sort_keys=True).encode(),
            on_delivery=delivery,
        )

    def poll(self, seconds):
        self.producer.poll(seconds)

    def flush(self, seconds):
        return self.producer.flush(seconds)

    def observe(self, context):
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '5s'")
            rows = connection.execute(self.options["query"], {"run_id": context.run_id}).fetchall()
        return [
            dict(row, id=str(row["id"]), completed_at=row["completed_at"].isoformat())
            for row in rows
        ]

    def close(self):
        if self.producer is not None:
            self.producer.purge()
            self.producer.poll(0)
            self.producer = None


class Orders(KafkaSQL):
    """Use the existing order consumer unchanged, behind the engine boundary.

    This initial bridge checks immutable facts; the legacy inspector remains the
    authority for projection ordering and DLQ-specific checks.
    """

    def __init__(self, options):
        super().__init__(
            options
            | {
                "topic": "orders.events.v1",
                "query": """SELECT event_id::text AS id,
                jsonb_build_object('event_type', event_type, 'order_amount', order_amount::text)
                    AS value, ingested_at AS completed_at
                FROM order_event WHERE run_id = %(run_id)s""",
            }
        )

    def prepare(self, context):
        from psycopg.types.json import Jsonb

        from streamscale.topics import ensure_topics

        ensure_topics(self.bootstrap)
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            connection.execute(
                """INSERT INTO experiment_run
                (run_id, mode, scenario, seed, started_at, git_commit, configuration)
                VALUES (%s, 'fixed', 'steady', %s, clock_timestamp(), %s, %s)""",
                (
                    UUID(context.run_id),
                    context.seed,
                    context.target.get("revision", "unspecified"),
                    Jsonb({"engine": True, "target": context.target}),
                ),
            )
        super().prepare(context)

    def send(self, record, acknowledge):
        import json
        import zlib

        payload = record["payload"]
        order_id = record["id"]
        wire = dict(
            run_id=record["run_id"],
            event_id=record["id"],
            order_id=order_id,
            event_type=payload["event_type"],
            order_amount=payload["order_amount"],
            schema_version=1,
            event_time=record["produced_at"],
            produced_at=record["produced_at"],
        )
        key = order_id.encode()

        def delivery(error, message):
            acknowledge(
                {
                    "id": record["id"],
                    "error": type(error).__name__ if error else None,
                    "partition": message.partition(),
                    "offset": message.offset(),
                }
            )

        self.producer.produce(
            self.options["topic"],
            key=key,
            value=json.dumps(wire).encode(),
            partition=zlib.crc32(key) % 3,
            on_delivery=delivery,
        )


class Memory:
    """Deterministic test transport; its results are never cloud capacity evidence."""

    def __init__(self, options):
        self.options = options
        self.rows = []

    def prepare(self, context):
        self.rows = []

    def send(self, record, acknowledge):
        from datetime import UTC, datetime

        if self.options.get("send_delay"):
            time.sleep(self.options["send_delay"])
        acknowledge({"id": record["id"], "error": None})
        if not self.options.get("drop"):
            self.rows.append(
                {
                    "id": record["id"],
                    "value": record["payload"],
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )

    def poll(self, seconds):
        time.sleep(seconds)

    def flush(self, seconds):
        return 0

    def observe(self, context):
        return list(self.rows)

    def close(self):
        pass
