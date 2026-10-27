"""The adapter boundary and strict, versioned scenario contract."""

import importlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Context:
    run_id: str
    directory: str
    label: str
    seed: int = 42
    target: dict = field(default_factory=dict)


class Adapter(Protocol):
    """Implementations must bound their I/O; close must tolerate partial preparation."""

    def prepare(self, context: Context) -> None: ...
    def send(self, record: dict, acknowledge: Callable) -> None: ...
    def poll(self, seconds: float) -> None: ...
    def flush(self, seconds: float) -> int: ...
    def observe(self, context: Context) -> list[dict]: ...
    def close(self) -> None: ...


def symbol(path: str):
    module, name = path.split(":", 1)
    return getattr(importlib.import_module(module), name)


def validate(config: dict) -> None:
    allowed = {
        "version",
        "seed",
        "adapter",
        "options",
        "payloads",
        "expected",
        "phases",
        "warmup_seconds",
        "drain_seconds",
        "poll_seconds",
        "max_lateness_seconds",
        "min_achieved_fraction",
        "ack_timeout_seconds",
        "hooks",
        "target",
        "thresholds",
    }
    if set(config) - allowed or config.get("version") != 1:
        raise ValueError("Unknown scenario field or unsupported version")
    if not isinstance(config.get("seed", 42), int):
        raise ValueError("seed must be an integer")
    if not isinstance(config.get("target"), dict) or not config["target"]:
        raise ValueError("target must describe the tested revision and environment")
    if not isinstance(config.get("adapter"), str) or ":" not in config["adapter"]:
        raise ValueError("adapter must be module:factory")
    if not isinstance(config.get("options", {}), dict):
        raise ValueError("options must be an object")
    if not isinstance(config.get("payloads"), list) or not config["payloads"]:
        raise ValueError("payloads must be a nonempty list of objects")
    if any(not isinstance(p, dict) for p in config["payloads"]):
        raise ValueError("payloads must contain objects")
    phases = config.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("phases must be nonempty")
    for phase in phases:
        if set(phase) != {"seconds", "rate"}:
            raise ValueError("Each phase needs seconds and rate")
        for key in ("seconds", "rate"):
            positive(phase[key], key)
        if phase["seconds"] * phase["rate"] < 1:
            raise ValueError("Each phase must schedule at least one event")
    defaults = {
        "warmup_seconds": 0,
        "drain_seconds": 30,
        "poll_seconds": 0.1,
        "max_lateness_seconds": 0.5,
        "min_achieved_fraction": 0.95,
        "ack_timeout_seconds": 30,
    }
    for key, default in defaults.items():
        positive(config.get(key, default), key, zero=key == "warmup_seconds")
    if config.get("min_achieved_fraction", 0.95) > 1:
        raise ValueError("min_achieved_fraction must be at most one")
    thresholds = config.get("thresholds", {})
    if set(thresholds) - {"p95_seconds", "drain_seconds"}:
        raise ValueError("Unknown threshold")
    for key, value in thresholds.items():
        positive(value, key)
    if config.get("expected") is not None and ":" not in config["expected"]:
        raise ValueError("expected must be module:function")
    for phase, hook in config.get("hooks", {}).items():
        if phase not in {"before_load", "fault", "cleanup"}:
            raise ValueError("Unknown hook phase")
        if set(hook) - {"argv", "timeout_seconds", "at_seconds"}:
            raise ValueError("Unknown hook field")
        if not isinstance(hook.get("argv"), list) or not hook["argv"]:
            raise ValueError("Hook argv must be a nonempty list")
        if any(not isinstance(a, str) for a in hook["argv"]):
            raise ValueError("Hook arguments must be strings")
        positive(hook.get("timeout_seconds", 10), "hook timeout")
        if phase == "fault":
            positive(hook.get("at_seconds", 0), "fault time", zero=True)
            if hook.get("at_seconds", 0) >= sum(p["seconds"] for p in phases):
                raise ValueError("Fault must occur during the load window")


def positive(value, name, zero=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not zero)
    ):
        raise ValueError(f"{name} must be a finite {'nonnegative' if zero else 'positive'} number")
