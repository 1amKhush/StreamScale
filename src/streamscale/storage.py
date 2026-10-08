"""One transaction for a new fact and its conditional projection; safe on replay."""

import hashlib
import time
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from streamscale.contract import timestamp

STATUSES = {
    "ORDER_CREATED": "CREATED",
    "PAYMENT_CONFIRMED": "PAID",
    "ORDER_CANCELLED": "CANCELLED",
}


class IntegrityFailure(RuntimeError):
    """A valid-looking record conflicts with durable identity or routing."""


@dataclass(frozen=True)
class WriteResult:
    outcome: str
    state_applied: bool


class EventStore:
    def __init__(self, database_url: str, attempts: int = 3):
        if not 1 <= attempts <= 10:
            raise ValueError("Database attempts must be 1–10")
        self.database_url = database_url
        self.attempts = attempts
        self.connection = None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connect(self):
        if self.connection is None or self.connection.closed:
            self.connection = psycopg.connect(
                self.database_url,
                autocommit=True,
                connect_timeout=10,
            )
            self.connection.execute("SET statement_timeout = '15s'")
            self.connection.execute("SET lock_timeout = '5s'")
        return self.connection

    def persist(self, event: dict, partition: int, offset: int) -> WriteResult:
        if not 0 <= partition <= 2 or offset < 0:
            raise IntegrityFailure("Source coordinate is outside the v1 contract")
        for attempt in range(self.attempts):
            try:
                return self._persist(event, partition, offset)
            except (psycopg.OperationalError, psycopg.InterfaceError):
                self.close()
                if attempt == self.attempts - 1:
                    raise
                time.sleep(min(0.2 * 2**attempt, 2))
            except psycopg.errors.UniqueViolation as error:
                raise IntegrityFailure(
                    "Source coordinate already belongs to another event"
                ) from error
            except psycopg.errors.ForeignKeyViolation as error:
                raise IntegrityFailure(
                    "Run must be registered before its events are consumed"
                ) from error
        raise RuntimeError("No database write attempts configured")

    def _persist(self, event: dict, partition: int, offset: int) -> WriteResult:
        run, identity, order = (UUID(event[field]) for field in ("run_id", "event_id", "order_id"))
        event_time = timestamp(event["event_time"])
        amount = Decimal(event["order_amount"])
        logical = (run, order, event["event_type"], event_time, amount, 1, partition)
        connection = self._connect()
        lock = int.from_bytes(
            hashlib.blake2b(f"{run}:{order}".encode(), digest_size=8).digest(), signed=True
        )
        with connection.transaction():
            # Serialize one order even when its state row does not exist yet.
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            inserted = connection.execute(
                """INSERT INTO order_event
                   (event_id, run_id, order_id, event_type, event_time, produced_at,
                    order_amount, schema_version, kafka_partition, kafka_offset, accepted_payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                (
                    identity,
                    run,
                    order,
                    event["event_type"],
                    event_time,
                    timestamp(event["produced_at"]),
                    amount,
                    partition,
                    offset,
                    Jsonb(event),
                ),
            ).fetchone()
            if inserted is None:
                stored = connection.execute(
                    """SELECT run_id, order_id, event_type, event_time,
                              order_amount, schema_version, kafka_partition
                       FROM order_event WHERE event_id = %s""",
                    (identity,),
                ).fetchone()
                if stored != logical:
                    raise IntegrityFailure(
                        "Existing event ID has different logical fields or partition"
                    )
                coordinate = connection.execute(
                    """SELECT event_id FROM order_event
                       WHERE run_id = %s AND kafka_partition = %s AND kafka_offset = %s""",
                    (run, partition, offset),
                ).fetchone()
                if coordinate is not None and coordinate[0] != identity:
                    raise IntegrityFailure("Source coordinate already belongs to another event")
                return WriteResult("duplicate", False)
            previous = connection.execute(
                "SELECT last_kafka_partition FROM order_state WHERE run_id = %s AND order_id = %s",
                (run, order),
            ).fetchone()
            if previous is not None and previous[0] != partition:
                raise IntegrityFailure("All events for one run/order must share a partition")
            applied = connection.execute(
                """INSERT INTO order_state
                   (run_id, order_id, status, order_amount, last_event_id,
                    last_event_time, last_kafka_partition, last_kafka_offset)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (run_id, order_id) DO UPDATE SET
                       status = EXCLUDED.status,
                       order_amount = EXCLUDED.order_amount,
                       last_event_id = EXCLUDED.last_event_id,
                       last_event_time = EXCLUDED.last_event_time,
                       last_kafka_partition = EXCLUDED.last_kafka_partition,
                       last_kafka_offset = EXCLUDED.last_kafka_offset,
                       updated_at = clock_timestamp()
                   WHERE (EXCLUDED.last_event_time, EXCLUDED.last_kafka_offset)
                         > (order_state.last_event_time, order_state.last_kafka_offset)
                   RETURNING last_event_id""",
                (
                    run,
                    order,
                    STATUSES[event["event_type"]],
                    amount,
                    identity,
                    event_time,
                    partition,
                    offset,
                ),
            ).fetchone()
            return WriteResult("accepted", applied is not None)

    def finish_run(self, run_id: str):
        self._connect().execute(
            """UPDATE experiment_run SET ended_at = COALESCE(ended_at, clock_timestamp())
               WHERE run_id = %s""",
            (UUID(run_id),),
        )
