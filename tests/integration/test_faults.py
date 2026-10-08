"""Live faults isolated from the user's DB: unavailable endpoint, crash and group churn."""

import base64
import os
import socket
import subprocess
import sys
import time
from threading import Event, Thread
from uuid import UUID

import psycopg
import pytest
from confluent_kafka import Consumer, TopicPartition
from psycopg.conninfo import make_conninfo
from streamscale_workload.generator import generate
from streamscale_workload.publisher import publish

from streamscale.config import Settings
from streamscale.consumer import consume
from streamscale.contract import validate_event
from streamscale.dlq import DeadLetterQueue
from streamscale.inspection import inspect_run
from streamscale.run_artifacts import offsets, read_acknowledgements, read_run
from streamscale.storage import EventStore
from streamscale.topics import DLQ_TOPIC, INPUT_TOPIC

pytestmark = pytest.mark.integration


def group_offsets(settings, run):
    probe = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": run["group_id"],
            "enable.auto.commit": False,
        }
    )
    try:
        return {
            str(p.partition): p.offset
            for p in probe.committed([TopicPartition(INPUT_TOPIC, p) for p in range(3)], timeout=10)
        }
    finally:
        probe.close()


def test_database_outage_exhausts_bounded_retries_without_dlq_or_offset_advance(
    settings,
    scratch_database,
    tmp_path,
    monkeypatch,
):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "outage"
    generate(directory, orders=2)
    publish(directory, local, paced=False)
    run = read_run(directory)
    dlq_before = offsets(local.kafka_bootstrap_servers, DLQ_TOPIC)
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        # Bound but not listening: real connection refused, without stopping the shared DB.
        broken = make_conninfo(
            scratch_database, host="127.0.0.1", port=unavailable.getsockname()[1]
        )
        attempts = []
        connect = EventStore._connect

        def counted(store):
            attempts.append(time.monotonic())
            return connect(store)

        monkeypatch.setattr(EventStore, "_connect", counted)
        with pytest.raises(psycopg.OperationalError):
            consume(Settings(local.kafka_bootstrap_servers, broken), directory=directory)
    assert len(attempts) == 3
    assert attempts[1] - attempts[0] >= 0.19
    assert attempts[2] - attempts[1] >= 0.39
    assert group_offsets(local, run) == run["start_offsets"]
    assert offsets(local.kafka_bootstrap_servers, DLQ_TOPIC) == dlq_before
    with psycopg.connect(scratch_database) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM order_event WHERE run_id=%s", (UUID(run["run_id"]),)
            ).fetchone()[0]
            == 0
        )
    consume(local, directory=directory)
    assert inspect_run(directory, local)["status"] == "passed"


def test_lost_database_connection_reconnects_and_commits_once(
    settings,
    scratch_database,
    tmp_path,
):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "reconnect"
    generate(directory, orders=1)
    publish(directory, local, paced=False)
    ack = read_acknowledgements(directory, read_run(directory))[0]
    event = validate_event(
        base64.b64decode(ack["payload_base64"]), base64.b64decode(ack["key_base64"])
    )
    store = EventStore(scratch_database)
    try:
        dead = store._connect()
        pid = dead.info.backend_pid
        with psycopg.connect(scratch_database, autocommit=True) as admin:
            assert admin.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
        assert store.persist(event, ack["partition"], ack["offset"]).outcome == "accepted"
        assert store.connection.info.backend_pid != pid
        assert store.persist(event, ack["partition"], ack["offset"]).outcome == "duplicate"
    finally:
        store.close()


def test_real_crash_after_dlq_ack_repeats_envelope_but_not_source_or_sql_facts(
    settings,
    scratch_database,
    tmp_path,
):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "dlq-crash"
    generate(directory, orders=2, profile="fixtures")
    publish(directory, local, paced=False)
    run = read_run(directory)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamscale.cli",
            "consume",
            "--run-dir",
            str(directory),
            "--crash-after-dlq-ack",
            "1",
        ],
        env=os.environ
        | {
            "DATABASE_URL": scratch_database,
            "KAFKA_BOOTSTRAP_SERVERS": local.kafka_bootstrap_servers,
        },
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 76, result.stderr
    from streamscale.inspection import _dead_letters

    # The acknowledged invalid record's source offset is still uncommitted.
    entries = _dead_letters(local, run)
    assert len(entries) == 1
    source = entries[0]["source"]
    assert group_offsets(local, run)[str(source["partition"])] <= source["offset"]
    consume(local, directory=directory)
    inspected = inspect_run(directory, local)
    assert inspected["status"] == "passed", inspected
    assert inspected["dlq_copies"] == 3
    assert inspected["dlq_unique_sources"] == 2
    assert inspected["stored_facts"] == 4
    assert inspected["state_regressions"] == 0


def test_live_join_leave_rebalance_preserves_facts_and_projection(
    settings,
    scratch_database,
    tmp_path,
    monkeypatch,
):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "rebalance"
    generate(directory, orders=400)
    publish(directory, local, paced=False)
    assignments = [[], []]
    stops = [Event(), Event()]
    failures, summaries = [], []
    persist = EventStore.persist

    def paced(store, *args):
        time.sleep(0.015)  # Keep a real backlog while the second member joins.
        return persist(store, *args)

    monkeypatch.setattr(EventStore, "persist", paced)

    def worker(index):
        try:
            summaries.append(
                consume(
                    local,
                    directory=directory,
                    batch_size=1,
                    stay_running=True,
                    stop=stops[index],
                    audit_log=tmp_path / f"member-{index}.jsonl",
                    on_assignment=lambda parts: assignments[index].append(parts),
                )
            )
        except Exception as error:
            failures.append(error)

    def until(predicate, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert not failures, failures
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError(f"Timed out waiting for group churn: {assignments}")

    threads = [Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    try:
        threads[0].start()
        until(lambda: assignments[0] and len(assignments[0][-1]) == 3)
        threads[1].start()
        until(lambda: all(history and 0 < len(history[-1]) < 3 for history in assignments))
        assert set(assignments[0][-1]).isdisjoint(assignments[1][-1])
        assert set(assignments[0][-1] + assignments[1][-1]) == {0, 1, 2}
        stops[0].set()
        threads[0].join(20)
        until(lambda: assignments[1] and len(assignments[1][-1]) == 3)
        until(
            lambda: group_offsets(local, read_run(directory)) == read_run(directory)["end_offsets"]
        )
    finally:
        for stop in stops:
            stop.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(20)
    assert not failures, failures
    assert all(not thread.is_alive() for thread in threads)
    assert sum(summary["accepted"] for summary in summaries) == 800
    inspected = inspect_run(directory, local)
    assert inspected["status"] == "passed", inspected
    assert inspected["stored_facts"] == 800
    assert inspected["state_rows"] == 400


def test_live_dlq_delivery_failure_does_not_commit_invalid_source(
    settings,
    scratch_database,
    tmp_path,
    monkeypatch,
):
    from streamscale import consumer as module
    from streamscale import dlq as dlq_module

    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "dlq-outage"
    generate(directory, orders=2, profile="fixtures")
    publish(directory, local, paced=False)
    run = read_run(directory)
    before = offsets(local.kafka_bootstrap_servers, DLQ_TOPIC)
    configuration = dlq_module.producer_config
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        with monkeypatch.context() as fault:
            fault.setattr(
                dlq_module,
                "producer_config",
                lambda server: (
                    configuration(server)
                    | {
                        "message.timeout.ms": 1000,
                    }
                ),
            )
            broken = DeadLetterQueue(f"127.0.0.1:{unavailable.getsockname()[1]}")
            fault.setattr(module, "DeadLetterQueue", lambda server: broken)
            with pytest.raises(RuntimeError, match="DLQ was not acknowledged"):
                consume(local, directory=directory)
    committed = group_offsets(local, run)
    for ack in read_acknowledgements(directory, run):
        if not ack["valid"]:
            assert committed[str(ack["partition"])] <= ack["offset"]
    assert offsets(local.kafka_bootstrap_servers, DLQ_TOPIC) == before
    consume(local, directory=directory)
    assert inspect_run(directory, local)["status"] == "passed"
