"""Inspect facts/state and reconcile local acknowledgements and invalid outcomes."""

import base64
import json
import time
from collections import Counter
from pathlib import Path
from uuid import UUID

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition

from streamscale.config import Settings
from streamscale.contract import timestamp
from streamscale.run_artifacts import (
    offsets,
    read_acknowledgements,
    read_run,
    verify_manifest,
    write_json,
)
from streamscale.storage import STATUSES
from streamscale.topics import DLQ_TOPIC, INPUT_TOPIC, PARTITIONS


def _dead_letters(settings: Settings, run: dict) -> list[dict]:
    end = offsets(settings.kafka_bootstrap_servers, DLQ_TOPIC)
    active = {
        part for part in range(PARTITIONS) if run["dlq_start_offsets"][str(part)] < end[str(part)]
    }
    if not active:
        return []
    reader = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": "streamscale.dlq-inspection",
            "enable.auto.commit": False,
            "enable.partition.eof": True,
        }
    )
    rows = []
    try:
        reader.assign(
            [
                TopicPartition(DLQ_TOPIC, part, run["dlq_start_offsets"][str(part)])
                for part in active
            ]
        )
        deadline = time.monotonic() + 15
        while active and time.monotonic() < deadline:
            message = reader.poll(0.5)
            if message is None:
                continue
            part = message.partition()
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    if message.offset() >= end[str(part)]:
                        active.discard(part)
                    continue
                raise KafkaException(message.error())
            if message.offset() >= end[str(part)]:
                active.discard(part)
                continue
            rows.append(json.loads(message.value()))
            if message.offset() + 1 >= end[str(part)]:
                active.discard(part)
        if active:
            raise RuntimeError("Timed out reading the captured DLQ range")
    finally:
        reader.close()
    return rows


def inspect_run(directory: Path, settings: Settings, group_id: str | None = None) -> dict:
    run = read_run(directory)
    attempts = verify_manifest(directory, run)
    if run["status"] != "published":
        raise RuntimeError("This run did not publish successfully; inspect its partial artifacts")
    acknowledgements = read_acknowledgements(directory, run)
    group_id = group_id or run["group_id"]
    valid = [ack for ack in acknowledgements if ack["valid"]]
    expected_ids = {ack["event_id"] for ack in valid}
    # Deliberate duplicates never replace the fact's original source offset.
    first = {}
    for ack in sorted(valid, key=lambda ack: (ack["partition"], ack["offset"])):
        first.setdefault(ack["event_id"], ack)
    expected_state = {}
    for ack in first.values():
        event = json.loads(base64.b64decode(ack["payload_base64"]))
        pair = (timestamp(event["event_time"]), ack["offset"])
        order = event["order_id"]
        if order not in expected_state or pair > expected_state[order][0]:
            expected_state[order] = (
                pair,
                event["event_id"],
                ack["partition"],
                STATUSES[event["event_type"]],
                event["order_amount"],
            )
    with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
        facts = connection.execute(
            "SELECT event_id::text FROM order_event WHERE run_id = %s", (UUID(run["run_id"]),)
        ).fetchall()
        state = connection.execute(
            """SELECT order_id::text, last_event_time, last_kafka_offset,
                      last_event_id::text, last_kafka_partition, status, order_amount::text
               FROM order_state WHERE run_id = %s ORDER BY order_id""",
            (UUID(run["run_id"]),),
        ).fetchall()
    actual_ids = {row[0] for row in facts}
    actual_state = {row[0]: ((row[1], row[2]), row[3], row[4], row[5], row[6]) for row in state}
    regressions = sum(
        actual_state.get(order) != expected for order, expected in expected_state.items()
    )
    regressions += len(actual_state.keys() - expected_state.keys())
    invalid = {
        (ack["partition"], ack["offset"]): ack for ack in acknowledgements if not ack["valid"]
    }
    dlq = [
        envelope
        for envelope in _dead_letters(settings, run)
        if (envelope["source"]["partition"], envelope["source"]["offset"]) in invalid
    ]
    dlq_coordinates = {(e["source"]["partition"], e["source"]["offset"]) for e in dlq}
    bad_envelopes = 0
    for envelope in dlq:
        ack = invalid[(envelope["source"]["partition"], envelope["source"]["offset"])]
        if (
            envelope["original_key_base64"] != ack["key_base64"]
            or envelope["original_payload_base64"] != ack["payload_base64"]
            or envelope["source"]["topic"] != INPUT_TOPIC
        ):
            bad_envelopes += 1
    reader = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
        }
    )
    try:
        committed = reader.committed(
            [TopicPartition(INPUT_TOPIC, part) for part in range(PARTITIONS)], timeout=10
        )
    finally:
        reader.close()
    lag = {
        str(part.partition): max(0, run["end_offsets"][str(part.partition)] - part.offset)
        for part in committed
    }
    result = {
        "run_id": run["run_id"],
        "group_id": group_id,
        "acknowledged": len(acknowledgements),
        "expected_valid_unique": len(expected_ids),
        "stored_facts": len(facts),
        "missing_valid_ids": sorted(expected_ids - actual_ids),
        "unexpected_valid_ids": sorted(actual_ids - expected_ids),
        "duplicate_stored_ids": len(facts) - len(actual_ids),
        "duplicate_attempts": sum(ack["duplicate"] for ack in acknowledgements),
        "state_rows": len(state),
        "state_regressions": regressions,
        "invalid_attempts": len(invalid),
        "dlq_copies": len(dlq),
        "dlq_unique_sources": len(dlq_coordinates),
        "dlq_error_codes": dict(Counter(e["error_code"] for e in dlq)),
        "missing_dlq_sources": len(invalid.keys() - dlq_coordinates),
        "invalid_dlq_envelopes": bad_envelopes,
        "remaining_run_lag": lag,
        "states": [
            {
                "order_id": row[0],
                "status": row[5],
                "amount": row[6],
                "last_event_id": row[3],
                "last_event_time": row[1].isoformat(),
                "partition": row[4],
                "offset": row[2],
            }
            for row in state
        ],
    }
    passed = (
        len(acknowledgements) == len(attempts)
        and not result["missing_valid_ids"]
        and not result["unexpected_valid_ids"]
        and result["duplicate_stored_ids"] == 0
        and regressions == 0
        and result["missing_dlq_sources"] == 0
        and bad_envelopes == 0
        and not any(lag.values())
    )
    result["status"] = "passed" if passed else "failed"
    write_json(directory / "inspection.json", result)
    return result
