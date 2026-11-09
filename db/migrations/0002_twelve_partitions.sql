-- Widen source coordinates without rewriting facts or changing event identity.
ALTER TABLE order_event DROP CONSTRAINT order_event_kafka_partition_check;
ALTER TABLE order_event ADD CONSTRAINT order_event_kafka_partition_check
    CHECK (kafka_partition BETWEEN 0 AND 11);
ALTER TABLE order_state DROP CONSTRAINT order_state_last_kafka_partition_check;
ALTER TABLE order_state ADD CONSTRAINT order_state_last_kafka_partition_check
    CHECK (last_kafka_partition BETWEEN 0 AND 11);
