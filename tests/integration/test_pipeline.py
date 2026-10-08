import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from confluent_kafka import Consumer, TopicPartition
from psycopg.types.json import Jsonb
from streamscale_workload.generator import generate, logical_workload, wire_record
from streamscale_workload.publisher import publish

from streamscale.config import Settings
from streamscale.consumer import consume
from streamscale.contract import timestamp, validate_event
from streamscale.inspection import inspect_run
from streamscale.run_artifacts import read_acknowledgements, read_run
from streamscale.storage import STATUSES, EventStore, IntegrityFailure
from streamscale.topics import INPUT_TOPIC

pytestmark = pytest.mark.integration


def registered_event(database):
    run = uuid4()
    attempt = logical_workload(42, 1, "steady", "normal", run)[0]
    key, payload = wire_record(
        attempt, str(run), datetime(2026, 1, 1, tzinfo=UTC), "2026-01-01T00:00:01Z"
    )
    with psycopg.connect(database) as connection:
        connection.execute(
            """INSERT INTO experiment_run
               (run_id, mode, scenario, seed, started_at, git_commit, configuration)
               VALUES (%s, 'fixed', 'steady', 42, now(), %s, %s)""",
            (run, "0" * 40, Jsonb({"test": True})),
        )
    return validate_event(payload, key)


def test_live_seeded_correctness_workload_reconciles(settings, scratch_database, tmp_path):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "correctness"
    generate(directory, seed=42, orders=200, profile="correctness")
    assert publish(directory, local, paced=False)["acknowledged"] == 408
    with pytest.raises(RuntimeError, match="already published"):
        publish(directory, local, paced=False)
    run = read_run(directory)
    for ack in read_acknowledgements(directory, run):
        assert timestamp(ack["produced_at"]) >= timestamp(run["publish_started_at"])
        if ack["valid"]:
            event = json.loads(base64.b64decode(ack["payload_base64"]))
            assert event["produced_at"] == ack["produced_at"]
    summary = consume(local, directory=directory)
    assert summary["accepted"] == 396
    assert summary["duplicate"] == 8
    assert summary["invalid"] == 4
    assert summary["status"] == "drained"
    result = inspect_run(directory, local)
    assert result["status"] == "passed", result
    assert result["stored_facts"] == 396
    assert result["state_rows"] == 200
    assert result["state_regressions"] == 0
    assert result["dlq_unique_sources"] == 4
    assert set(result["dlq_error_codes"]) == {
        "INVALID_JSON",
        "INVALID_ENCODING",
        "INVALID_AMOUNT",
        "KEY_MISMATCH",
    }


def test_real_crash_after_db_commit_replays_without_duplicate_rows(
    settings, scratch_database, tmp_path
):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "crash"
    generate(directory, seed=42, orders=2, profile="fixtures")
    publish(directory, local, paced=False)
    run = read_run(directory)
    environment = os.environ | {
        "DATABASE_URL": scratch_database,
        "KAFKA_BOOTSTRAP_SERVERS": local.kafka_bootstrap_servers,
    }
    crashed = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamscale.cli",
            "consume",
            "--run-dir",
            str(directory),
            "--crash-after-db-commit",
            "1",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert crashed.returncode == 75, crashed.stderr
    with psycopg.connect(scratch_database) as connection:
        facts = connection.execute(
            """SELECT event_id::text, kafka_partition, kafka_offset
               FROM order_event WHERE run_id = %s""",
            (UUID(run["run_id"]),),
        ).fetchall()
        assert len(facts) == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM order_state WHERE run_id = %s", (UUID(run["run_id"]),)
            ).fetchone()[0]
            == 1
        )
    probe = Consumer(
        {
            "bootstrap.servers": local.kafka_bootstrap_servers,
            "group.id": run["group_id"],
            "enable.auto.commit": False,
        }
    )
    try:
        position = probe.committed([TopicPartition(INPUT_TOPIC, facts[0][1])], timeout=10)[0].offset
        assert position == run["start_offsets"][str(facts[0][1])]
        assert position < facts[0][2] + 1
    finally:
        probe.close()
    restarted = consume(local, directory=directory)
    assert restarted["accepted"] == 3
    assert restarted["duplicate"] == 2  # One injected duplicate and the crashed record's replay.
    assert restarted["invalid"] == 2
    result = inspect_run(directory, local)
    assert result["status"] == "passed", result
    assert result["stored_facts"] == 4
    assert result["state_rows"] == 2
    assert result["duplicate_stored_ids"] == 0
    with psycopg.connect(scratch_database) as connection:
        completed_at = connection.execute(
            "SELECT ended_at FROM experiment_run WHERE run_id = %s", (UUID(run["run_id"]),)
        ).fetchone()[0]
    assert completed_at is not None
    # A second group replays every source record: no additional facts or state regression.
    replay_group = "streamscale.replay." + uuid4().hex
    replay = consume(local, directory=directory, group_id=replay_group)
    assert replay["accepted"] == 0
    assert replay["duplicate"] == 5
    second = inspect_run(directory, local, replay_group)
    assert second["status"] == "passed", second
    assert second["dlq_copies"] == 4
    assert second["dlq_unique_sources"] == 2
    with psycopg.connect(scratch_database) as connection:
        assert (
            connection.execute(
                "SELECT ended_at FROM experiment_run WHERE run_id = %s", (UUID(run["run_id"]),)
            ).fetchone()[0]
            == completed_at
        )


def test_projection_failure_rolls_back_fact_in_same_transaction(scratch_database, monkeypatch):
    event = registered_event(scratch_database)
    store = EventStore(scratch_database)
    monkeypatch.setitem(STATUSES, "ORDER_CREATED", "INVALID_STATUS")
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            store.persist(event, 0, 0)
        with psycopg.connect(scratch_database) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM order_event WHERE run_id = %s", (UUID(event["run_id"]),)
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT count(*) FROM order_state WHERE run_id = %s", (UUID(event["run_id"]),)
                ).fetchone()[0]
                == 0
            )
    finally:
        store.close()


def test_conflicting_event_identity_is_not_a_normal_duplicate(scratch_database):
    event = registered_event(scratch_database)
    store = EventStore(scratch_database)
    try:
        assert store.persist(event, 0, 0).outcome == "accepted"
        with pytest.raises(IntegrityFailure, match="different logical"):
            store.persist(event | {"order_amount": "999.99"}, 0, 1)
        assert (
            store.persist(event | {"produced_at": "2026-01-01T00:00:02Z"}, 0, 1).outcome
            == "duplicate"
        )
    finally:
        store.close()


def test_partition_change_rolls_back_new_fact(scratch_database):
    event = registered_event(scratch_database)
    store = EventStore(scratch_database)
    try:
        store.persist(event, 0, 0)
        with pytest.raises(IntegrityFailure, match="share a partition"):
            store.persist(
                event | {"event_id": str(uuid4()), "event_type": "PAYMENT_CONFIRMED"}, 1, 0
            )
        with psycopg.connect(scratch_database) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM order_event WHERE run_id = %s", (UUID(event["run_id"]),)
                ).fetchone()[0]
                == 1
            )
    finally:
        store.close()


def test_source_coordinate_conflict_is_not_acknowledged_as_duplicate(scratch_database):
    event = registered_event(scratch_database)
    store = EventStore(scratch_database)
    try:
        store.persist(event, 0, 0)
        with pytest.raises(IntegrityFailure, match="Source coordinate"):
            store.persist(event | {"event_id": str(uuid4())}, 0, 0)
        other = event | {"event_id": str(uuid4()), "event_type": "PAYMENT_CONFIRMED"}
        store.persist(other, 0, 1)
        # ON CONFLICT(event_id) alone must not hide a conflicting source coordinate.
        with pytest.raises(IntegrityFailure, match="Source coordinate"):
            store.persist(event, 0, 1)
    finally:
        store.close()
