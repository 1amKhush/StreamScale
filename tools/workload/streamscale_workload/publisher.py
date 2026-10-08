"""Publish a generated execution once; log only real broker acknowledgements."""

import hashlib
import json
import os
import time
import zlib
from pathlib import Path
from uuid import UUID

import psycopg
from confluent_kafka import Producer
from psycopg.types.json import Jsonb

from streamscale.config import Settings
from streamscale.contract import ContractError, timestamp, validate_event
from streamscale.run_artifacts import (
    offsets,
    read_run,
    revision_snapshot,
    utc_now,
    verify_manifest,
    write_json,
)
from streamscale.topics import DLQ_TOPIC, INPUT_TOPIC, PARTITIONS, ensure_topics, producer_config
from streamscale_workload.generator import encoded, wire_record


def publish(directory: Path, settings: Settings, paced: bool = True) -> dict:
    run = read_run(directory)
    if run["status"] != "generated":
        raise RuntimeError(
            "This execution was already published or attempted; generate a fresh directory/run"
        )
    attempts = verify_manifest(directory, run)
    ensure_topics(settings.kafka_bootstrap_servers)
    run.update(revision_snapshot())
    run["start_offsets"] = offsets(settings.kafka_bootstrap_servers, INPUT_TOPIC)
    run["dlq_start_offsets"] = offsets(settings.kafka_bootstrap_servers, DLQ_TOPIC)
    run["publish_started_at"] = utc_now()
    run["paced"] = paced
    run["status"] = "publishing"
    run["acknowledged"] = 0
    write_json(directory / "run.json", run)
    try:
        with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
            connection.execute(
                """INSERT INTO experiment_run
                   (run_id, mode, scenario, seed, started_at, git_commit, configuration)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    UUID(run["run_id"]),
                    run["mode"],
                    run["scenario"],
                    run["seed"],
                    timestamp(run["publish_started_at"]),
                    run["git_commit"],
                    Jsonb(run),
                ),
            )
        producer = Producer(producer_config(settings.kafka_bootstrap_servers))
        failures = []
        acknowledgements = 0
        last_checkpoint = time.monotonic()
        clock_start = time.monotonic()
        anchor = timestamp(run["publish_started_at"])
        with (directory / "acknowledgements.jsonl").open("x", encoding="utf-8") as log:

            def delivery(attempt, key, payload, produced_at):
                def acknowledged(error, message):
                    nonlocal acknowledgements, last_checkpoint
                    if error:
                        failures.append(attempt["attempt_index"])
                        return
                    expected_partition = zlib.crc32(key) % PARTITIONS
                    if message.partition() != expected_partition:
                        failures.append(attempt["attempt_index"])
                    log.write(
                        json.dumps(
                            {
                                "attempt_index": attempt["attempt_index"],
                                "event_id": attempt["event_id"],
                                "valid": not attempt["fault"].startswith("invalid_"),
                                "duplicate": attempt["duplicate"],
                                "fault": attempt["fault"],
                                "produced_at": produced_at,
                                "partition": message.partition(),
                                "offset": message.offset(),
                                "key_base64": encoded(key),
                                "payload_base64": encoded(payload),
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    log.flush()
                    acknowledgements += 1
                    if time.monotonic() - last_checkpoint >= 1:
                        run["acknowledged"] = acknowledgements
                        write_json(directory / "run.json", run)
                        last_checkpoint = time.monotonic()

                return acknowledged

            for attempt in attempts:
                if paced:
                    target = clock_start + attempt["scheduled_at_us"] / 1_000_000
                    while (remaining := target - time.monotonic()) > 0:
                        producer.poll(min(remaining, 0.1))
                deadline = time.monotonic() + 30
                while True:
                    produced_at = utc_now()
                    key, payload = wire_record(attempt, run["run_id"], anchor, produced_at)
                    try:
                        validate_event(payload, key)
                    except ContractError:
                        if not attempt["fault"].startswith("invalid_"):
                            raise
                    else:
                        if attempt["fault"].startswith("invalid_"):
                            raise RuntimeError(
                                "Requested fault unexpectedly produced a valid event"
                            )
                    try:
                        producer.produce(
                            INPUT_TOPIC,
                            key=key,
                            value=payload,
                            on_delivery=delivery(attempt, key, payload, produced_at),
                        )
                        break
                    except BufferError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Kafka producer queue did not recover") from None
                        producer.poll(0.1)
                producer.poll(0)
            if producer.flush(30) or failures or acknowledgements != len(attempts):
                raise RuntimeError(
                    "Publishing failed; acknowledgement log contains the confirmed subset"
                )
            log.flush()
            os.fsync(log.fileno())
        run["end_offsets"] = offsets(settings.kafka_bootstrap_servers, INPUT_TOPIC)
        if (
            sum(run["end_offsets"][part] - start for part, start in run["start_offsets"].items())
            != acknowledgements
        ):
            raise RuntimeError("Concurrent publishing changed this run's captured source range")
        run["publish_ended_at"] = utc_now()
        run["acknowledged"] = acknowledgements
        run["acknowledgements_sha256"] = hashlib.sha256(
            (directory / "acknowledgements.jsonl").read_bytes()
        ).hexdigest()
        run["status"] = "published"
        with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
            connection.execute(
                "UPDATE experiment_run SET configuration = %s WHERE run_id = %s",
                (Jsonb(run), UUID(run["run_id"])),
            )
        write_json(directory / "run.json", run)
    except BaseException:
        run["status"] = "failed"
        write_json(directory / "run.json", run)
        raise
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "acknowledged": acknowledgements,
        "directory": str(directory),
    }
