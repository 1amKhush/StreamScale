import base64
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest

from streamscale.contract import ContractError, timestamp, validate_event, validator
from streamscale.ids import MAX_SEED, event_id, new_run_id, order_id

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/order-created.v1.json").read_text(encoding="utf-8")
)


def check(event):
    return validate_event(json.dumps(event).encode(), FIXTURE["order_id"].encode())


@pytest.mark.parametrize("kind", ["ORDER_CREATED", "PAYMENT_CONFIRMED", "ORDER_CANCELLED"])
def test_accepted_event_types(kind):
    event = deepcopy(FIXTURE)
    event["event_type"] = kind
    assert check(event) == event


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("schema_version", 2, "UNSUPPORTED_SCHEMA_VERSION"),
        ("schema_version", 0, "SCHEMA_VALIDATION_FAILED"),
        ("schema_version", True, "SCHEMA_VALIDATION_FAILED"),
        ("schema_version", 1.0, "SCHEMA_VALIDATION_FAILED"),
        ("event_type", "REFUNDED", "UNSUPPORTED_EVENT_TYPE"),
        ("event_type", None, "SCHEMA_VALIDATION_FAILED"),
        ("order_amount", -1, "INVALID_AMOUNT"),
        ("order_amount", 13.37, "INVALID_AMOUNT"),
        ("order_amount", "01.00", "INVALID_AMOUNT"),
        ("order_amount", "1.001", "INVALID_AMOUNT"),
        ("order_amount", "10000000000000000.00", "INVALID_AMOUNT"),
        ("event_time", "2026-01-01T00:00:00", "INVALID_TIMESTAMP"),
        ("event_time", "2026-02-30T00:00:00Z", "INVALID_TIMESTAMP"),
        ("event_time", "2026-01-01T00:00:00.1234567Z", "INVALID_TIMESTAMP"),
        ("event_time", "2026-01-01T00:00:60Z", "INVALID_TIMESTAMP"),
        ("event_time", "2026-01-01T00:00:00-00:00", "INVALID_TIMESTAMP"),
        ("event_time", "2026-01-01T00:00:00+00:60", "INVALID_TIMESTAMP"),
        ("produced_at", None, "INVALID_TIMESTAMP"),
        ("run_id", "not-a-uuid", "SCHEMA_VALIDATION_FAILED"),
        ("customer", "unknown field", "SCHEMA_VALIDATION_FAILED"),
    ],
)
def test_rejected_contract_mutations(field, value, code):
    event = deepcopy(FIXTURE)
    event[field] = value
    with pytest.raises(ContractError) as caught:
        check(event)
    assert caught.value.code == code


@pytest.mark.parametrize("field", list(FIXTURE))
def test_every_field_required(field):
    event = deepcopy(FIXTURE)
    del event[field]
    with pytest.raises(ContractError) as caught:
        check(event)
    assert caught.value.code == "SCHEMA_VALIDATION_FAILED"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"\xff", "INVALID_ENCODING"),
        (b"{", "INVALID_JSON"),
        (b'{"a": 1, "a": 2}', "INVALID_JSON"),
        (b'{"a": NaN}', "INVALID_JSON"),
        (None, "INVALID_JSON"),
        (b"[]", "SCHEMA_VALIDATION_FAILED"),
    ],
)
def test_rejected_wire_records(payload, code):
    with pytest.raises(ContractError) as caught:
        validate_event(payload, None)
    assert caught.value.code == code


@pytest.mark.parametrize("key", [None, b"", b"another-order", b"\xff"])
def test_kafka_key_must_match_order(key):
    with pytest.raises(ContractError) as caught:
        validate_event(json.dumps(FIXTURE).encode(), key)
    assert caught.value.code == "KEY_MISMATCH"


@pytest.mark.parametrize("amount", ["0.00", "9999999999999999.99"])
def test_exact_amount_boundaries(amount):
    event = deepcopy(FIXTURE)
    event["order_amount"] = amount
    assert check(event)["order_amount"] == amount


def test_offset_timestamps_preserve_instants_and_late_records_are_valid():
    event = deepcopy(FIXTURE)
    event["event_time"] = "2025-12-31T23:00:00-01:00"
    assert check(event) == event
    assert timestamp(event["event_time"]) == timestamp(FIXTURE["event_time"])
    assert timestamp(event["event_time"]) < timestamp(event["produced_at"])


def test_same_seed_keeps_order_ids_but_new_runs_have_distinct_event_ids():
    first, second = new_run_id(), new_run_id()
    assert first != second
    logical_orders = [order_id(42, i) for i in range(100)]
    assert logical_orders == [order_id(42, i) for i in range(100)]
    assert logical_orders != [order_id(43, i) for i in range(100)]
    first_ids = {event_id(first, i) for i in range(100)}
    assert len(first_ids) == 100
    assert first_ids.isdisjoint({event_id(second, i) for i in range(100)})
    assert event_id(first, 7) == event_id(first, 7)  # Deliberate duplicate uses original index.


def test_committed_fixture_pins_uuid_derivation():
    assert str(order_id(42, 0)) == FIXTURE["order_id"]
    assert str(event_id(UUID(FIXTURE["run_id"]), 0)) == FIXTURE["event_id"]


@pytest.mark.parametrize("seed", [-1, MAX_SEED + 1, True, 1.5])
def test_invalid_seeds(seed):
    with pytest.raises(ValueError):
        order_id(seed, 0)


def test_dlq_contract_preserves_binary_and_absent_bytes():
    envelope = {
        "schema_version": 1,
        "original_key_base64": None,
        "original_payload_base64": base64.b64encode(b"\xff\x00").decode("ascii"),
        "source": {"topic": "orders.events.v1", "partition": 2, "offset": 17},
        "error_code": "INVALID_ENCODING",
        "detail": "Payload is not UTF-8",
        "failed_at": FIXTURE["produced_at"],
    }
    validator("dlq-envelope.v1.json").validate(envelope)
    assert base64.b64decode(envelope["original_payload_base64"], validate=True) == b"\xff\x00"
    envelope["original_payload_base64"] = "not base64"
    assert not validator("dlq-envelope.v1.json").is_valid(envelope)


def test_schema_is_draft_2020_12():
    for name in ("order-event.v1.json", "dlq-envelope.v1.json"):
        assert validator(name).schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert UUID(FIXTURE["event_id"])
