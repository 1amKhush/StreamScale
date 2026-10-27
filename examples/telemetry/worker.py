"""Independent telemetry application: Kafka envelope -> temperature conversion -> SQL.

No StreamScale imports, schemas, migrations, consumer code or business tables.
"""

import argparse
import json
import os
import signal
from threading import Event

import psycopg
from confluent_kafka import Consumer
from psycopg.types.json import Jsonb


def convert(payload):
    # Integer arithmetic keeps the demonstration reproducible across platforms.
    return {"fahrenheit_milli": payload["celsius_milli"] * 9 // 5 + 32000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--drop-every", type=int, default=0)
    args = parser.parse_args()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    consumer = Consumer(
        {
            "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
            "group.id": args.group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    try:
        with psycopg.connect(
            os.environ["DATABASE_URL"], autocommit=True, connect_timeout=5
        ) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS telemetry_reading (
                sample_id text PRIMARY KEY, experiment text NOT NULL, reading jsonb NOT NULL,
                completed_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
            consumer.subscribe([args.topic])
            while not stop.is_set():
                message = consumer.poll(0.2)
                if message is None:
                    continue
                if message.error():
                    raise RuntimeError("Kafka receive failed")
                event = json.loads(message.value())
                # Intentional regression: ack some messages without storing their results.
                dropped = args.drop_every and (event["index"] + 1) % args.drop_every == 0
                if not dropped:
                    with connection.transaction():
                        connection.execute(
                            """INSERT INTO telemetry_reading
                            (sample_id, experiment, reading) VALUES (%s, %s, %s)
                            ON CONFLICT (sample_id) DO NOTHING""",
                            (event["id"], event["run_id"], Jsonb(convert(event["payload"]))),
                        )
                consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
