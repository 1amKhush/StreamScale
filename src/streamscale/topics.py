"""Explicit bootstrap; existing topic drift is a failure, never silently repaired."""

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic, ResourceType

INPUT_TOPIC = "orders.events.v1"
DLQ_TOPIC = "orders.events.dlq.v1"
PARTITIONS = 3
TOPIC_CONFIG = {"cleanup.policy": "delete", "retention.ms": "604800000"}


def ensure_topics(bootstrap_servers: str) -> dict[str, int]:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers, "socket.timeout.ms": 10000})
    definitions = [
        NewTopic(name, num_partitions=PARTITIONS, replication_factor=1, config=TOPIC_CONFIG)
        for name in (INPUT_TOPIC, DLQ_TOPIC)
    ]
    futures = admin.create_topics(definitions, request_timeout=15)
    for future in futures.values():
        try:
            future.result(timeout=20)
        except KafkaException as exc:
            if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
    metadata = admin.list_topics(timeout=15)
    counts = {}
    for name in (INPUT_TOPIC, DLQ_TOPIC):
        topic = metadata.topics.get(name)
        if topic is None or topic.error:
            raise RuntimeError(f"Topic {name} is unavailable after bootstrap")
        count = len(topic.partitions)
        if count != PARTITIONS:
            raise RuntimeError(
                f"Topic {name} has {count} partitions; contract requires {PARTITIONS}"
            )
        if any(len(part.replicas) != 1 for part in topic.partitions.values()):
            raise RuntimeError(f"Topic {name} must have replication factor 1 for this local stack")
        counts[name] = count
    resources = [ConfigResource(ResourceType.TOPIC, name) for name in counts]
    for resource, future in admin.describe_configs(resources, request_timeout=15).items():
        config = future.result(timeout=20)
        for key, expected in TOPIC_CONFIG.items():
            if config[key].value != expected:
                raise RuntimeError(f"Topic {resource.name} configuration drift: {key}")
    return counts


def producer_config(bootstrap_servers: str) -> dict[str, str | int | bool]:
    return {
        "bootstrap.servers": bootstrap_servers,
        "acks": "all",
        "enable.idempotence": True,
        "partitioner": "consistent",
        "message.timeout.ms": 15000,
    }
