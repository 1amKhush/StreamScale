"""Transport boundaries: real acknowledgements, run binding and read-only SQL."""

from datetime import UTC, datetime
from types import SimpleNamespace

from streamscale.engine.adapters import KafkaSQL, Orders
from streamscale.engine.model import Context


def test_kafka_sql_normalizes_output_and_runs_read_only(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, query, params=None):
            calls.append((query, params))
            return SimpleNamespace(
                fetchall=lambda: [
                    {"id": "sample", "value": {"x": 1}, "completed_at": datetime.now(UTC)}
                ]
            )

    monkeypatch.setenv("DATABASE_URL", "test-dsn")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "test-broker")
    monkeypatch.setattr("streamscale.engine.adapters.psycopg.connect", lambda *a, **k: Connection())
    adapter = KafkaSQL({"topic": "custom-input", "query": "SELECT ... WHERE run=%(run_id)s"})
    rows = adapter.observe(Context("run-123", "/tmp/example", "test"))
    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    assert "statement_timeout" in calls[1][0]
    assert calls[2][1] == {"run_id": "run-123"}
    assert rows[0]["completed_at"].endswith("+00:00")


def test_input_only_acknowledges_after_broker_callback(monkeypatch):
    import json

    calls = []
    received = []
    monkeypatch.setenv("DATABASE_URL", "test-dsn")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "test-broker")
    adapter = KafkaSQL({"topic": "custom-input", "query": "unused"})
    adapter.producer = SimpleNamespace(produce=lambda *args, **kwargs: calls.append((args, kwargs)))
    record = {"id": "a", "run_id": "run", "payload": {"x": 1}}
    adapter.send(record, received.append)
    assert received == []
    assert json.loads(calls[0][1]["value"]) == record
    message = SimpleNamespace(topic=lambda: "custom-input", partition=lambda: 2, offset=lambda: 10)
    calls[0][1]["on_delivery"](None, message)
    assert received[0]["id"] == "a"
    assert received[0]["offset"] == 10
    assert received[0]["error"] is None


def test_order_bridge_emits_the_existing_contract(monkeypatch):
    import json
    from uuid import uuid4

    from streamscale.contract import validate_event

    calls = []
    monkeypatch.setenv("DATABASE_URL", "test-dsn")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "test-broker")
    adapter = Orders({})
    adapter.producer = SimpleNamespace(produce=lambda *args, **kwargs: calls.append((args, kwargs)))
    record = {
        "id": str(uuid4()),
        "run_id": str(uuid4()),
        "payload": {"event_type": "ORDER_CREATED", "order_amount": "12.00"},
        "produced_at": datetime.now(UTC).isoformat(),
    }
    adapter.send(record, lambda _: None)
    args, kwargs = calls[0]
    assert args[0] == "orders.events.v1"
    event = validate_event(kwargs["value"], kwargs["key"])
    assert event["event_id"] == record["id"]
    assert json.loads(kwargs["value"])["order_amount"] == "12.00"
