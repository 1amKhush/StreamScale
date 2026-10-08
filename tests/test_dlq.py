from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from confluent_kafka import KafkaError

from streamscale import dlq
from streamscale.contract import ContractError


def test_native_delivery_error_is_reported_without_comparing_it_to_none(monkeypatch):
    producer = Mock()

    def flush(timeout):
        callback = producer.produce.call_args.kwargs["on_delivery"]
        callback(KafkaError(KafkaError._MSG_TIMED_OUT), None)
        return 0

    producer.flush.side_effect = flush
    monkeypatch.setattr(dlq, "Producer", lambda config: producer)
    queue = dlq.DeadLetterQueue("localhost:9092")
    message = SimpleNamespace(
        key=lambda: b"key",
        value=lambda: b"\xff",
        topic=lambda: "orders.events.v1",
        partition=lambda: 0,
        offset=lambda: 10,
    )
    with pytest.raises(RuntimeError, match="source offset remains uncommitted"):
        queue.publish(message, ContractError("INVALID_ENCODING", "not UTF-8"))
