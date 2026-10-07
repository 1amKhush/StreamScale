"""Executable M0 decoding and validation, independent of Kafka and PostgreSQL."""

import json
import re
from datetime import datetime
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from streamscale.assets import asset_directory

EVENT_TYPES = {"ORDER_CREATED", "PAYMENT_CONFIRMED", "ORDER_CANCELLED"}
AMOUNT_PATTERN = re.compile(r"(0|[1-9][0-9]{0,15})\.[0-9]{2}\Z")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z"
)


class ContractError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail[:256]
        super().__init__(f"{code}: {self.detail}")


def timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError("Expected timezone-aware RFC 3339 with at most six fractional digits")
    if value.endswith("-00:00"):
        raise ValueError("Unknown local timezone offset is not supported")
    # fromisoformat also rejects nonexistent dates, times, offsets and leap seconds.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Missing timezone offset")
    return parsed


FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return True  # Type validation belongs to the schema.
    timestamp(value)
    return True


@lru_cache(maxsize=2)
def validator(name: str = "order-event.v1.json") -> Draft202012Validator:
    if name not in {"order-event.v1.json", "dlq-envelope.v1.json"}:
        raise ValueError("Unknown contract schema")
    schema = json.loads((asset_directory("schemas") / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FORMATS)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON object member")
        result[name] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-finite numbers are not JSON")


def validate_event(payload: bytes | None, key: bytes | None) -> dict[str, Any]:
    if payload is None:
        raise ContractError("INVALID_JSON", "Null payload is not an order event")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContractError("INVALID_ENCODING", "Payload is not UTF-8") from exc
    try:
        event = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        raise ContractError("INVALID_JSON", "Malformed or ambiguous JSON") from exc
    if not isinstance(event, dict):
        raise ContractError("SCHEMA_VALIDATION_FAILED", "Event must be a JSON object")
    version = event.get("schema_version")
    if type(version) is int and version > 0 and version != 1:
        raise ContractError("UNSUPPORTED_SCHEMA_VERSION", "Only schema_version 1 is supported")
    kind = event.get("event_type")
    if isinstance(kind, str) and kind not in EVENT_TYPES:
        raise ContractError("UNSUPPORTED_EVENT_TYPE", "Event type is unsupported")
    if "order_amount" in event and (
        not isinstance(event["order_amount"], str)
        or not AMOUNT_PATTERN.fullmatch(event["order_amount"])
    ):
        raise ContractError("INVALID_AMOUNT", "Use a nonnegative decimal string with two places")
    for field in ("event_time", "produced_at"):
        if field in event:
            try:
                timestamp(event[field])
            except ValueError as exc:
                raise ContractError("INVALID_TIMESTAMP", f"{field}: {exc}") from exc
    if type(version) is not int:
        raise ContractError("SCHEMA_VALIDATION_FAILED", "schema_version must be a JSON integer")
    error = next(validator().iter_errors(event), None)
    if error:
        path = ".".join(str(part) for part in error.absolute_path) or "event"
        raise ContractError("SCHEMA_VALIDATION_FAILED", f"{path}: {error.validator} check failed")
    if key != event["order_id"].encode("ascii"):
        raise ContractError("KEY_MISMATCH", "Key must be the canonical order_id in UTF-8")
    return event
