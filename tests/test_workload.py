from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from streamscale_workload.generator import generate, logical_workload, schedule_us, wire_record

from streamscale.contract import ContractError, validate_event
from streamscale.run_artifacts import manifest_hash, verify_manifest


def test_seed_reproduces_logical_workload_with_fresh_execution_ids(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    one = generate(first, seed=42, orders=100, profile="correctness")
    two = generate(second, seed=42, orders=100, profile="correctness")
    a, b = verify_manifest(first, one), verify_manifest(second, two)
    assert one["run_id"] != two["run_id"]
    assert one["logical_sha256"] == two["logical_sha256"]
    assert one["manifest_sha256"] != two["manifest_sha256"]
    assert {row["event_id"] for row in a}.isdisjoint({row["event_id"] for row in b})
    assert [row["order_id"] for row in a] == [row["order_id"] for row in b]
    assert one["attempts"] == 204
    assert one["invalid_attempts"] == 2
    assert one["duplicate_attempts"] == 4
    assert one["valid_unique"] == 198


def test_another_seed_changes_the_logical_workload():
    run = uuid4()
    assert manifest_hash(
        logical_workload(42, 10, "steady", "normal", run), logical=True
    ) != manifest_hash(logical_workload(43, 10, "steady", "normal", run), logical=True)


def test_fixture_profile_covers_normal_invalid_duplicate_late_and_equal_time():
    run = uuid4()
    attempts = logical_workload(42, 2, "steady", "fixtures", run)
    assert len(attempts) == 7
    by_index = {a["logical_index"]: a for a in attempts if not a["duplicate"]}
    assert by_index[0]["event_time_us"] < by_index[1]["event_time_us"]
    assert by_index[0]["attempt_index"] > by_index[1]["attempt_index"]
    assert by_index[2]["event_time_us"] == by_index[3]["event_time_us"]
    assert by_index[2]["attempt_index"] < by_index[3]["attempt_index"]
    duplicate = next(a for a in attempts if a["duplicate"])
    assert duplicate["event_id"] == by_index[1]["event_id"]
    valid, invalid = [], []
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    for attempt in attempts:
        key, payload = wire_record(attempt, str(run), anchor, "2026-01-01T00:00:01Z")
        try:
            valid.append(validate_event(payload, key))
        except ContractError as error:
            invalid.append(error.code)
    assert len(valid) == 5
    assert invalid == ["INVALID_JSON", "INVALID_ENCODING"]


def test_duplicate_wire_attempts_only_change_actual_publish_time():
    run = uuid4()
    attempt = logical_workload(42, 1, "steady", "normal", run)[0]
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    key, first = wire_record(attempt, str(run), anchor, "2026-01-01T00:00:01Z")
    _, second = wire_record(attempt, str(run), anchor, "2026-01-01T00:00:02Z")
    a, b = validate_event(first, key), validate_event(second, key)
    assert a.pop("produced_at") != b.pop("produced_at")
    assert a == b


@pytest.mark.parametrize(
    "fault", ["invalid_json", "invalid_encoding", "invalid_amount", "invalid_key"]
)
def test_injected_fault_is_actually_invalid(fault):
    run = uuid4()
    attempt = logical_workload(42, 1, "steady", "normal", run)[0] | {"fault": fault}
    key, payload = wire_record(
        attempt, str(run), datetime(2026, 1, 1, tzinfo=UTC), "2026-01-01T00:00:01Z"
    )
    with pytest.raises(ContractError):
        validate_event(payload, key)


def test_amounts_stay_exact_and_stable_within_an_order():
    attempts = logical_workload(42, 100, "steady", "normal", uuid4())
    counts = Counter(a["order_id"] for a in attempts)
    assert set(counts.values()) == {2}
    for first, second in zip(attempts[::2], attempts[1::2], strict=True):
        assert first["order_amount"] == second["order_amount"]


def test_scenario_phase_boundaries():
    assert schedule_us(3000, "flash-sale") == 30_000_000
    assert schedule_us(25500, "flash-sale") == 45_000_000
    assert schedule_us(90000, "overload-recovery") == 60_000_000
    with pytest.raises(ValueError):
        schedule_us(12000, "steady")


def test_generation_refuses_overwrite_and_manifest_tampering(tmp_path):
    directory = tmp_path / "run"
    run = generate(directory)
    with pytest.raises(FileExistsError):
        generate(directory)
    path = directory / "manifest.jsonl"
    path.write_text(path.read_text().replace('"none"', '"late"', 1))
    with pytest.raises(RuntimeError, match="checksum"):
        verify_manifest(directory, run)
