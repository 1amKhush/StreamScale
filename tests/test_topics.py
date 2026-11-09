from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from confluent_kafka import KafkaError, KafkaException

from streamscale import topics


def future(value=None, error=None):
    result = Future()
    if error:
        result.set_exception(error)
    else:
        result.set_result(value)
    return result


def admin_fixture(monkeypatch, partitions=12, replicas=1, config=None, create_error=None):
    admin = Mock()
    admin.create_topics.return_value = {
        name: future(error=create_error) for name in (topics.INPUT_TOPIC, topics.DLQ_TOPIC)
    }
    admin.list_topics.return_value = SimpleNamespace(
        topics={
            name: SimpleNamespace(
                error=None,
                partitions={
                    i: SimpleNamespace(replicas=list(range(replicas))) for i in range(partitions)
                },
            )
            for name in (topics.INPUT_TOPIC, topics.DLQ_TOPIC)
        }
    )
    effective_config = topics.TOPIC_CONFIG | (config or {})
    admin.describe_configs.side_effect = lambda resources, **kwargs: {
        resource: future(
            {key: SimpleNamespace(value=value) for key, value in effective_config.items()}
        )
        for resource in resources
    }
    monkeypatch.setattr(topics, "AdminClient", lambda settings: admin)
    return admin


def test_existing_correct_topics_are_idempotent(monkeypatch):
    admin_fixture(
        monkeypatch, create_error=KafkaException(KafkaError(KafkaError.TOPIC_ALREADY_EXISTS))
    )
    assert topics.ensure_topics("unused:9092") == {
        topics.INPUT_TOPIC: 12,
        topics.DLQ_TOPIC: 12,
    }


@pytest.mark.parametrize("partitions", [1, 2, 3, 4, 13])
def test_refuses_existing_partition_drift(monkeypatch, partitions):
    admin_fixture(monkeypatch, partitions=partitions)
    with pytest.raises(RuntimeError, match="contract requires 12"):
        topics.ensure_topics("unused:9092")


def test_refuses_configuration_drift(monkeypatch):
    admin_fixture(monkeypatch, config={"cleanup.policy": "compact"})
    with pytest.raises(RuntimeError, match="configuration drift"):
        topics.ensure_topics("unused:9092")


def test_refuses_wrong_local_replication(monkeypatch):
    admin_fixture(monkeypatch, replicas=2)
    with pytest.raises(RuntimeError, match="replication factor 1"):
        topics.ensure_topics("unused:9092")


def test_does_not_hide_broker_creation_failure(monkeypatch):
    admin_fixture(
        monkeypatch, create_error=KafkaException(KafkaError(KafkaError.TOPIC_AUTHORIZATION_FAILED))
    )
    with pytest.raises(KafkaException):
        topics.ensure_topics("unused:9092")


def expansion_fixture(monkeypatch, *, members=(), changing=False):
    admin = admin_fixture(monkeypatch, partitions=3)
    legacy = admin.list_topics.return_value
    expanded = SimpleNamespace(
        topics={
            name: SimpleNamespace(
                error=None, partitions={p: SimpleNamespace(replicas=[1]) for p in range(12)}
            )
            for name in (topics.INPUT_TOPIC, topics.DLQ_TOPIC)
        }
    )
    admin.list_topics.side_effect = [legacy, expanded]
    admin.list_consumer_groups.return_value = future(
        SimpleNamespace(valid=[SimpleNamespace(group_id="old")], errors=[])
    )
    admin.describe_consumer_groups.return_value = {"old": future(SimpleNamespace(members=members))}
    admin.create_partitions.return_value = {"input": future(), "dlq": future()}
    probe = Mock()
    probe.get_watermark_offsets.side_effect = [(0, 10)] * 6 + [(0, 11 if changing else 10)] * 6
    monkeypatch.setattr(topics, "Consumer", lambda _: probe)
    monkeypatch.setattr(topics.time, "sleep", lambda _: None)
    return admin, probe


def test_expands_legacy_topics_without_deleting_or_recreating_them(monkeypatch):
    admin, probe = expansion_fixture(monkeypatch)
    assert topics.ensure_topics("unused", allow_expansion=True) == {
        topics.INPUT_TOPIC: 12,
        topics.DLQ_TOPIC: 12,
    }
    requests = admin.create_partitions.call_args.args[0]
    assert [(r.topic, r.new_total_count) for r in requests] == [
        (topics.INPUT_TOPIC, 12),
        (topics.DLQ_TOPIC, 12),
    ]
    admin.delete_topics.assert_not_called()
    probe.close.assert_called_once()


def test_active_consumers_block_partition_expansion(monkeypatch):
    member = SimpleNamespace(
        assignment=SimpleNamespace(topic_partitions=[SimpleNamespace(topic=topics.INPUT_TOPIC)])
    )
    admin, _ = expansion_fixture(monkeypatch, members=[member])
    with pytest.raises(RuntimeError, match="finish and drain sales"):
        topics.ensure_topics("unused", allow_expansion=True)
    admin.create_partitions.assert_not_called()


def test_active_publishers_block_partition_expansion(monkeypatch):
    admin, probe = expansion_fixture(monkeypatch, changing=True)
    with pytest.raises(RuntimeError, match="Publishers are active"):
        topics.ensure_topics("unused", allow_expansion=True)
    admin.create_partitions.assert_not_called()
    probe.close.assert_called_once()


def test_expansion_still_refuses_unknown_partition_counts(monkeypatch):
    admin = admin_fixture(monkeypatch, partitions=6)
    with pytest.raises(RuntimeError, match="contract requires 12"):
        topics.ensure_topics("unused", allow_expansion=True)
    admin.create_partitions.assert_not_called()
