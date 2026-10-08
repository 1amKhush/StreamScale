"""Bounded sequential batches, durable handling first, synchronous partition commits second."""

import json
import os
import time
from pathlib import Path
from threading import Event

from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition

from streamscale.config import Settings
from streamscale.contract import ContractError, validate_event
from streamscale.dlq import DeadLetterQueue
from streamscale.metrics import measured_database
from streamscale.run_artifacts import read_run, utc_now, verify_manifest, write_json
from streamscale.storage import EventStore, IntegrityFailure
from streamscale.topics import INPUT_TOPIC, ensure_topics


def handle_record(
    message,
    consumer,
    store,
    dlq,
    *,
    run_id=None,
    after_db_commit=None,
    after_dlq_ack=None,
    before_source_commit=None,
    metrics=None,
):
    """A failure at any stage returns before this partition's next offset is committed."""
    try:
        event = validate_event(message.value(), message.key())
    except ContractError as error:
        if metrics:
            metrics.invalid.inc()
        try:
            dlq.publish(message, error)
        except Exception:
            if metrics:
                metrics.failure("dlq")
            raise
        if metrics:
            metrics.dlq.inc()
        if after_dlq_ack is not None:
            after_dlq_ack()
        outcome = "invalid"
        applied = False
    else:
        if run_id is not None and event["run_id"] != run_id:
            raise IntegrityFailure("Another run appeared inside the captured source range")
        result = measured_database(metrics, store, event, message.partition(), message.offset())
        outcome, applied = result.outcome, result.state_applied
        if after_db_commit is not None:
            after_db_commit()
    try:
        if before_source_commit is not None:
            before_source_commit()
    except Exception:
        if metrics:
            metrics.failure("ownership")
        raise
    try:
        committed = consumer.commit(
            offsets=[TopicPartition(message.topic(), message.partition(), message.offset() + 1)],
            asynchronous=False,
        )
        if any(partition.error is not None for partition in committed):
            raise RuntimeError("Kafka offset commit failed; restart this group for safe replay")
    except Exception:
        if metrics:
            metrics.failure("offset")
        raise
    return outcome, applied


def consume(
    settings: Settings,
    *,
    directory: Path | None = None,
    group_id: str | None = None,
    max_records: int | None = None,
    idle_timeout: float = 30,
    batch_size: int = 50,
    crash_after_db_commit: int | None = None,
    crash_after_dlq_ack: int | None = None,
    stay_running: bool = False,
    stop: Event | None = None,
    audit_log: Path | None = None,
    on_assignment=None,
    metrics=None,
    readiness=None,
    follow_publish: bool = False,
) -> dict:
    if not 1 <= batch_size <= 1000 or idle_timeout <= 0:
        raise ValueError("Batch size must be 1–1000 and idle timeout must be positive")
    if max_records is not None and max_records <= 0:
        raise ValueError("max-records must be positive")
    if crash_after_db_commit is not None and crash_after_db_commit <= 0:
        raise ValueError("crash-after-db-commit must be positive")
    if crash_after_dlq_ack is not None and crash_after_dlq_ack <= 0:
        raise ValueError("crash-after-dlq-ack must be positive")
    if group_id is not None and not group_id.strip():
        raise ValueError("Consumer group must not be empty")
    run = read_run(directory) if directory else None
    if run is not None:
        verify_manifest(directory, run)
        if run["status"] not in ({"publishing", "published"} if follow_publish else {"published"}):
            raise RuntimeError("Bounded consumption requires a successfully published run")
    group_id = group_id or (run["group_id"] if run else "streamscale.consumer.v1")
    ensure_topics(settings.kafka_bootstrap_servers)
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "allow.auto.create.topics": False,
            "enable.partition.eof": True,
            "max.poll.interval.ms": 300000,
            "session.timeout.ms": 10000,
            "heartbeat.interval.ms": 3000,
        }
    )
    store = EventStore(settings.database_url)
    dlq = DeadLetterQueue(settings.kafka_bootstrap_servers)
    assigned, finished = set(), set()
    durable = {}
    counts = {"accepted": 0, "duplicate": 0, "invalid": 0, "late": 0, "processed": 0}
    db_commits = 0
    dlq_acks = 0
    generation = 0
    assignments = 0
    completed = False
    stop = stop or Event()
    started = utc_now()

    def audit(action, **fields):
        if audit_log is not None:
            with audit_log.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"at": utc_now(), "action": action, **fields}) + "\n")

    def assignment(client, partitions):
        nonlocal generation, assignments
        generation += 1
        assignments += 1
        assigned.clear()
        finished.clear()
        durable.clear()
        committed = client.committed(partitions, timeout=10)
        floors = []
        for partition, saved in zip(partitions, committed, strict=True):
            assigned.add(partition.partition)
            if run is not None:
                start = run["start_offsets"][str(partition.partition)]
                end = run.get("end_offsets", {}).get(str(partition.partition))
                low, high = client.get_watermark_offsets(partition, timeout=10, cached=False)
                if start < low or (end is not None and end > high):
                    raise IntegrityFailure("Captured source records are no longer available")
                next_offset = max(start, saved.offset) if saved.offset >= 0 else start
                if next_offset > (end if end is not None else high):
                    raise IntegrityFailure(
                        "Group offset is beyond this run; use its original group"
                    )
                partition.offset = next_offset
                durable[partition.partition] = next_offset
                if next_offset == end:
                    finished.add(partition.partition)
                if saved.offset < start:
                    floors.append(TopicPartition(INPUT_TOPIC, partition.partition, start))
            elif saved.offset >= 0:
                partition.offset = saved.offset
        client.assign(partitions)
        if partitions:
            client.resume(partitions)
        # Run isolation deliberately starts after retained records from earlier executions.
        if floors and any(
            p.error is not None for p in client.commit(offsets=floors, asynchronous=False)
        ):
            raise RuntimeError("Could not commit the captured run start offsets")
        audit("assigned", generation=generation, partitions=sorted(assigned))
        if metrics:
            metrics.assignments.inc()
            metrics.owned.set(len(assigned))
        if readiness is not None and assigned:
            readiness.set()
        if on_assignment is not None:
            on_assignment(sorted(assigned))

    def revoke(client, partitions):
        nonlocal generation
        generation += 1
        audit("revoked", generation=generation, partitions=sorted(assigned))
        assigned.clear()
        finished.clear()
        durable.clear()
        if metrics:
            metrics.owned.set(0)
        if readiness is not None:
            readiness.clear()

    def after_database():
        nonlocal db_commits
        db_commits += 1
        if crash_after_db_commit == db_commits:
            # Explicit test-only injection: no finally, no close and no Kafka commit.
            os._exit(75)

    def after_dead_letter():
        nonlocal dlq_acks
        dlq_acks += 1
        if crash_after_dlq_ack == dlq_acks:
            os._exit(76)

    status = "stopped"
    last_progress = time.monotonic()
    next_metadata_poll = 0
    try:
        consumer.subscribe([INPUT_TOPIC], on_assign=assignment, on_revoke=revoke, on_lost=revoke)
        while not stop.is_set():
            if (
                follow_publish
                and run is not None
                and run["status"] == "publishing"
                and time.monotonic() >= next_metadata_poll
            ):
                next_metadata_poll = time.monotonic() + 1
                current = read_run(directory)
                if (
                    current["run_id"] != run["run_id"]
                    or current["start_offsets"] != run["start_offsets"]
                ):
                    raise IntegrityFailure("Active run/source start changed during publishing")
                if current["status"] == "failed":
                    raise RuntimeError("Publishing failed; this partial run is not acceptable")
                run.update(current)
                if run["status"] == "published":
                    for part in assigned:
                        end = run["end_offsets"][str(part)]
                        if durable[part] > end:
                            raise IntegrityFailure("Consumer advanced beyond the captured run end")
                        if durable[part] == end:
                            finished.add(part)
                            consumer.pause([TopicPartition(INPUT_TOPIC, part)])
            if run is not None and assigned and assigned <= finished:
                status = "drained"
                all_offsets = consumer.committed(
                    [TopicPartition(INPUT_TOPIC, part) for part in range(3)], timeout=10
                )
                if all(p.offset >= run["end_offsets"][str(p.partition)] for p in all_offsets):
                    if not completed:
                        store.finish_run(run["run_id"])
                        completed = True
                else:
                    status = "partition-drained"
                if not stay_running:
                    break
            if not stay_running and time.monotonic() - last_progress >= idle_timeout:
                if run is not None:
                    raise RuntimeError("Consumer did not drain the run before its idle timeout")
                status = "idle"
                break
            remaining = (
                batch_size
                if max_records is None
                else min(batch_size, max_records - counts["processed"])
            )
            batch = consumer.consume(num_messages=remaining, timeout=1)
            batch_generation = generation
            for message in batch:
                if stop.is_set():
                    break
                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(message.error())
                partition = message.partition()
                if generation != batch_generation or partition not in assigned:
                    # A revoked batch must never advance offsets for its former owner.
                    break
                if partition in durable and message.offset() < durable[partition]:
                    continue
                if (
                    run is not None
                    and "end_offsets" in run
                    and message.offset() >= run["end_offsets"][str(partition)]
                ):
                    consumer.pause([TopicPartition(INPUT_TOPIC, partition)])
                    continue

                def owns_partition(partition=partition, batch_generation=batch_generation):
                    if generation != batch_generation or partition not in assigned:
                        raise RuntimeError("Partition ownership changed; safe replay is required")

                outcome, applied = handle_record(
                    message,
                    consumer,
                    store,
                    dlq,
                    run_id=run["run_id"] if run else None,
                    after_db_commit=after_database,
                    after_dlq_ack=after_dead_letter,
                    before_source_commit=owns_partition,
                    metrics=metrics,
                )
                counts[outcome] += 1
                counts["processed"] += 1
                if outcome == "accepted" and not applied:
                    counts["late"] += 1
                durable[partition] = message.offset() + 1
                if (
                    run is not None
                    and "end_offsets" in run
                    and durable[partition] >= run["end_offsets"][str(partition)]
                ):
                    finished.add(partition)
                    consumer.pause([TopicPartition(INPUT_TOPIC, partition)])
                last_progress = time.monotonic()
            if max_records is not None and counts["processed"] >= max_records:
                status = "limit"
                break
    finally:
        if readiness is not None:
            readiness.clear()
        store.close()
        consumer.close()
    result = {
        "status": status,
        "group_id": group_id,
        "run_id": run["run_id"] if run else None,
        "started_at": started,
        "ended_at": utc_now(),
        "assignments": assignments,
        **counts,
    }
    if directory is not None:
        write_json(directory / "consumer-summary.json", result)
    return result
