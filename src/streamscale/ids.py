"""ID rules only; the seeded workload generator belongs to M2."""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

MAX_SEED = 2**63 - 1


def new_run_id() -> UUID:
    return uuid4()


def _index(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Logical index must be a nonnegative integer")


def order_id(seed: int, logical_order_index: int) -> UUID:
    if type(seed) is not int or not 0 <= seed <= MAX_SEED:
        raise ValueError("Seed must be an integer in [0, 2**63 - 1]")
    _index(logical_order_index)
    return uuid5(NAMESPACE_URL, f"streamscale:v1:seed:{seed}:order:{logical_order_index}")


def event_id(run_id: UUID, logical_event_index: int) -> UUID:
    _index(logical_event_index)
    return uuid5(run_id, f"streamscale:v1:event:{logical_event_index}")
