"""The basic invalid-record path: preserve bytes and await broker acknowledgement."""

import base64
import json

from confluent_kafka import Producer

from streamscale.contract import ContractError, validator
from streamscale.run_artifacts import utc_now
from streamscale.topics import DLQ_TOPIC, producer_config


def _encoded(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


class DeadLetterQueue:
    def __init__(self, bootstrap_servers: str):
        self.producer = Producer(producer_config(bootstrap_servers))

    def publish(self, message, error: ContractError):
        envelope = {
            "schema_version": 1,
            "original_key_base64": _encoded(message.key()),
            "original_payload_base64": _encoded(message.value()),
            "source": {
                "topic": message.topic(),
                "partition": message.partition(),
                "offset": message.offset(),
            },
            "error_code": error.code,
            "detail": error.detail,
            "failed_at": utc_now(),
        }
        validator("dlq-envelope.v1.json").validate(envelope)
        outcomes = []
        self.producer.produce(
            DLQ_TOPIC,
            key=message.key(),
            value=json.dumps(envelope, separators=(",", ":")).encode(),
            on_delivery=lambda error, message: outcomes.append(error),
        )
        # KafkaError equality against None can raise in the native binding.
        if self.producer.flush(20) or len(outcomes) != 1 or outcomes[0] is not None:
            raise RuntimeError("DLQ was not acknowledged; source offset remains uncommitted")
