import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest
from confluent_kafka import TopicPartition

from streamscale.consumer import handle_record
from streamscale.storage import IntegrityFailure, WriteResult
from streamscale.topics import INPUT_TOPIC

EVENT = json.loads((Path(__file__).parent / "fixtures/order-created.v1.json").read_text())


def record(payload=None):
    return SimpleNamespace(
        value=lambda: json.dumps(EVENT).encode() if payload is None else payload,
        key=lambda: EVENT["order_id"].encode(),
        topic=lambda: INPUT_TOPIC,
        partition=lambda: 2,
        offset=lambda: 10,
    )


def clients():
    consumer = Mock()
    consumer.commit.return_value = [TopicPartition(INPUT_TOPIC, 2, 11)]
    store = Mock()
    store.persist.return_value = WriteResult("accepted", True)
    return consumer, store, Mock()


def test_database_returns_committed_before_only_this_partition_offset_advances():
    consumer, store, dlq = clients()
    stages = []
    store.persist.side_effect = lambda *args: (
        stages.append("db-committed"),
        WriteResult("accepted", True),
    )[1]
    consumer.commit.side_effect = lambda **kwargs: (
        stages.append("offset-committed"),
        [TopicPartition(INPUT_TOPIC, 2, 11)],
    )[1]
    assert handle_record(record(), consumer, store, dlq) == ("accepted", True)
    assert stages == ["db-committed", "offset-committed"]
    kwargs = consumer.commit.call_args.kwargs
    assert kwargs["asynchronous"] is False
    assert [(p.topic, p.partition, p.offset) for p in kwargs["offsets"]] == [(INPUT_TOPIC, 2, 11)]
    dlq.publish.assert_not_called()


def test_db_failure_neither_commits_nor_sends_valid_event_to_dlq():
    consumer, store, dlq = clients()
    store.persist.side_effect = psycopg.OperationalError("Unavailable")
    with pytest.raises(psycopg.OperationalError):
        handle_record(record(), consumer, store, dlq)
    consumer.commit.assert_not_called()
    dlq.publish.assert_not_called()


def test_invalid_record_waits_for_dlq_ack_before_offset_commit():
    consumer, store, dlq = clients()
    stages = []
    dlq.publish.side_effect = lambda *args: stages.append("dlq-acknowledged")
    consumer.commit.side_effect = lambda **kwargs: (
        stages.append("offset-committed"),
        [TopicPartition(INPUT_TOPIC, 2, 11)],
    )[1]
    assert handle_record(record(b"\xff"), consumer, store, dlq) == ("invalid", False)
    assert stages == ["dlq-acknowledged", "offset-committed"]
    store.persist.assert_not_called()


def test_dlq_failure_preserves_source_offset():
    consumer, store, dlq = clients()
    dlq.publish.side_effect = RuntimeError("No acknowledgement")
    with pytest.raises(RuntimeError):
        handle_record(record(b"\xff"), consumer, store, dlq)
    consumer.commit.assert_not_called()
    store.persist.assert_not_called()


def test_failure_between_db_commit_and_offset_commit_is_replayable():
    consumer, store, dlq = clients()

    def crash():
        raise SystemExit(75)

    with pytest.raises(SystemExit):
        handle_record(record(), consumer, store, dlq, after_db_commit=crash)
    store.persist.assert_called_once()
    consumer.commit.assert_not_called()


def test_mismatched_run_is_not_silently_skipped():
    consumer, store, dlq = clients()
    with pytest.raises(IntegrityFailure):
        handle_record(record(), consumer, store, dlq, run_id="another-run")
    consumer.commit.assert_not_called()
    store.persist.assert_not_called()


def test_broker_commit_error_is_not_reported_as_handled():
    consumer, store, dlq = clients()
    consumer.commit.return_value = [SimpleNamespace(error="broker failure")]
    with pytest.raises(RuntimeError, match="offset commit failed"):
        handle_record(record(), consumer, store, dlq)


def test_dlq_ack_crash_leaves_source_replayable():
    consumer, store, dlq = clients()

    def crash():
        raise SystemExit(76)

    with pytest.raises(SystemExit):
        handle_record(record(b"\xff"), consumer, store, dlq, after_dlq_ack=crash)
    dlq.publish.assert_called_once()
    consumer.commit.assert_not_called()


def test_lost_ownership_after_database_write_prevents_offset_commit():
    consumer, store, dlq = clients()

    def lost():
        raise RuntimeError("Partition ownership changed")

    with pytest.raises(RuntimeError, match="ownership"):
        handle_record(record(), consumer, store, dlq, before_source_commit=lost)
    store.persist.assert_called_once()
    consumer.commit.assert_not_called()
