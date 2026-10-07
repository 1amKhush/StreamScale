CREATE TABLE experiment_run (
    run_id UUID PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('fixed', 'cpu-hpa', 'lag-keda')),
    scenario TEXT NOT NULL CHECK (scenario IN ('steady', 'flash-sale', 'overload-recovery')),
    seed BIGINT NOT NULL CHECK (seed >= 0),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ CHECK (ended_at >= started_at),
    git_commit TEXT NOT NULL CHECK (git_commit ~ '^[0-9a-f]{40}$'),
    configuration JSONB NOT NULL CHECK (jsonb_typeof(configuration) = 'object')
);

CREATE TABLE order_event (
    event_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES experiment_run(run_id),
    order_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('ORDER_CREATED', 'PAYMENT_CONFIRMED', 'ORDER_CANCELLED')
    ),
    event_time TIMESTAMPTZ NOT NULL,
    produced_at TIMESTAMPTZ NOT NULL,
    order_amount NUMERIC(18,2) NOT NULL CHECK (order_amount >= 0),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    kafka_partition INTEGER NOT NULL CHECK (kafka_partition BETWEEN 0 AND 2),
    kafka_offset BIGINT NOT NULL CHECK (kafka_offset >= 0),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    accepted_payload JSONB NOT NULL CHECK (jsonb_typeof(accepted_payload) = 'object'),
    UNIQUE (run_id, kafka_partition, kafka_offset)
);

CREATE INDEX order_event_run_time_idx ON order_event (run_id, event_time);
CREATE INDEX order_event_order_time_idx ON order_event (order_id, event_time DESC);

CREATE TABLE order_state (
    run_id UUID NOT NULL REFERENCES experiment_run(run_id),
    order_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CREATED', 'PAID', 'CANCELLED')),
    order_amount NUMERIC(18,2) NOT NULL CHECK (order_amount >= 0),
    last_event_id UUID NOT NULL REFERENCES order_event(event_id),
    last_event_time TIMESTAMPTZ NOT NULL,
    last_kafka_partition INTEGER NOT NULL CHECK (last_kafka_partition BETWEEN 0 AND 2),
    last_kafka_offset BIGINT NOT NULL CHECK (last_kafka_offset >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id, order_id)
);

CREATE FUNCTION reject_order_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'order_event is append-only; UPDATE and DELETE are forbidden'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER order_event_immutable
BEFORE UPDATE OR DELETE ON order_event
FOR EACH ROW EXECUTE FUNCTION reject_order_event_mutation();
