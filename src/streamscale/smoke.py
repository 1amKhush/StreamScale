"""M1 transport and DB check; no workload generator or service consumer."""

import json
import time
import zlib
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer, TopicPartition

from streamscale.config import Settings
from streamscale.contract import validate_event
from streamscale.ids import event_id, new_run_id, order_id
from streamscale.topics import INPUT_TOPIC, PARTITIONS, ensure_topics, producer_config


def _keys_for_partitions() -> dict[int, tuple[int, str]]:
    chosen = {}
    for index in range(1000):
        key = str(order_id(0, index))
        chosen.setdefault(zlib.crc32(key.encode()) % PARTITIONS, (index, key))
        if len(chosen) == PARTITIONS:
            return chosen
    raise RuntimeError("Could not select smoke keys covering every partition")


def smoke(settings: Settings, timeout: float = 30) -> dict:
    if timeout <= 0:
        raise ValueError("Smoke timeout must be positive")
    topics = ensure_topics(settings.kafka_bootstrap_servers)
    with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
        relations = ["experiment_run", "order_event", "order_state", "_streamscale_migration"]
        for relation in relations:
            if not connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0]:
                raise RuntimeError("Database is not bootstrapped; run streamscale migrate")
        if not connection.execute("SELECT name FROM _streamscale_migration").fetchall():
            raise RuntimeError("Database migration ledger is empty")
        connection.execute("CREATE TEMP TABLE streamscale_smoke (amount NUMERIC(18,2))")
        connection.execute("INSERT INTO streamscale_smoke VALUES (%s)", (Decimal("13.37"),))
        connection.commit()
        amount = connection.execute("SELECT amount FROM streamscale_smoke").fetchone()[0]
        if amount != Decimal("13.37"):
            raise RuntimeError("PostgreSQL decimal round trip failed")
        db_version = connection.execute("SHOW server_version").fetchone()[0]

    run_id = new_run_id()
    producer = Producer(producer_config(settings.kafka_bootstrap_servers))
    expected = {}
    delivered = {}
    failures = []

    def acknowledgement(error, message):
        if error:
            failures.append(str(error))
            return
        delivered[(message.partition(), message.offset())] = message.key().decode("ascii")

    for partition, (index, key) in sorted(_keys_for_partitions().items()):
        now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event = {
            "run_id": str(run_id),
            "event_id": str(event_id(run_id, index)),
            "order_id": key,
            "event_type": "ORDER_CREATED",
            "event_time": now,
            "produced_at": now,
            "order_amount": "13.37",
            "schema_version": 1,
        }
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        validate_event(payload, key.encode())
        expected[key] = (partition, payload)
        producer.produce(INPUT_TOPIC, key=key.encode(), value=payload, on_delivery=acknowledgement)
    remaining = producer.flush(timeout)
    if remaining or failures or len(delivered) != PARTITIONS:
        raise RuntimeError("Kafka publish acknowledgements incomplete")
    if {part for part, _ in delivered} != set(range(PARTITIONS)):
        raise RuntimeError("Key-based Kafka publishing did not cover all three partitions")

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"streamscale.smoke.{run_id}",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "enable.partition.eof": True,
            "allow.auto.create.topics": False,
        }
    )
    consumed = set()
    try:
        # Start at acknowledged coordinates, so retained records cannot satisfy this check.
        consumer.assign([TopicPartition(INPUT_TOPIC, part, offset) for part, offset in delivered])
        deadline = time.monotonic() + timeout
        while len(consumed) < PARTITIONS and time.monotonic() < deadline:
            message = consumer.poll(min(1, max(0, deadline - time.monotonic())))
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(message.error())
            coordinate = (message.partition(), message.offset())
            if coordinate not in delivered:
                continue
            key = message.key().decode("ascii")
            partition, payload = expected[key]
            if partition != message.partition() or payload != message.value():
                raise RuntimeError("Kafka consumed key, partition or payload differs from publish")
            validate_event(message.value(), message.key())
            consumed.add(coordinate)
    finally:
        consumer.close()
    if len(consumed) != PARTITIONS:
        raise RuntimeError("Timed out waiting for this smoke run's Kafka records")
    return {
        "status": "passed",
        "run_id": str(run_id),
        "topics": topics,
        "published": len(delivered),
        "consumed": len(consumed),
        "coordinates": [{"partition": part, "offset": offset} for part, offset in sorted(consumed)],
        "postgres_version": db_version,
        "postgres_decimal": str(amount),
        "business_tables_written": False,
        "source_offsets_committed": False,
    }
