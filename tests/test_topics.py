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


def admin_fixture(monkeypatch, partitions=3, replicas=1, config=None, create_error=None):
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
        topics.INPUT_TOPIC: 3,
        topics.DLQ_TOPIC: 3,
    }


@pytest.mark.parametrize("partitions", [1, 2, 4])
def test_refuses_existing_partition_drift(monkeypatch, partitions):
    admin_fixture(monkeypatch, partitions=partitions)
    with pytest.raises(RuntimeError, match="contract requires 3"):
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
