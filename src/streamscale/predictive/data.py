"""Allowlisted observations. Publisher plans and scenario metadata never enter features."""

import math
from collections import deque

import numpy as np

from streamscale.topics import PARTITIONS


def observation(sample):
    sources = sample.get("sources", {})
    at = sample["sampled_at"]
    for name in ("kafka", "kubernetes", "sql"):
        source = sources.get(name) or {}
        if not source.get("ok") or abs(at - source.get("at", 0)) > 5:
            raise ValueError(f"Missing or stale {name}")
    partitions = sorted(sample["kafka"]["partitions"], key=lambda p: p["partition"])
    if [p["partition"] for p in partitions] != list(range(PARTITIONS)):
        raise ValueError(f"Expected {PARTITIONS} partitions")
    high = [p["high"] for p in partitions]
    position = [p.get("position") for p in partitions]
    lag = [p.get("lag") for p in partitions]
    if any(v is None or not math.isfinite(v) or v < 0 for v in high + position + lag):
        raise ValueError("Unknown offsets or backlog")
    deployment = sample["kubernetes"]["consumer"]
    group = sample["kafka"].get("group") or {}
    return {
        "t": at,
        "run_id": (sample.get("run") or {}).get("run_id"),
        "high": high,
        "position": position,
        "lag": lag,
        "ready": deployment["ready"],
        "desired": deployment["desired"],
        "assignments": [m["partitions"] for m in group.get("members", [])],
        "stable": group.get("state") == "STABLE",
        "db_ms": (sample.get("sql") or {}).get("probe_ms"),
    }


class History:
    def __init__(self):
        self.rows = deque(maxlen=240)

    def append(self, row):
        if self.rows:
            old = self.rows[-1]
            dt = row["t"] - old["t"]
            if row["run_id"] != old["run_id"] or dt <= 0 or dt > 5:
                self.rows.clear()
            elif any(
                a < b
                for key in ("high", "position")
                for a, b in zip(row[key], old[key], strict=True)
            ):
                self.rows.clear()
        self.rows.append(row)

    def features(self, partition):
        rows = list(self.rows)
        if len(rows) < 2 or rows[-1]["t"] - rows[0]["t"] < 60:
            raise ValueError("Need 60 seconds of contiguous history")
        end = rows[-1]["t"]
        values = []
        for span in (5, 10, 20, 30, 60):
            before = [r for r in rows if r["t"] <= end - span]
            if not before:
                raise ValueError("Incomplete history")
            start = before[-1]
            values.append(
                (rows[-1]["high"][partition] - start["high"][partition]) / (end - start["t"])
            )
        # Only past arrival information; no clock/seed/scenario or future plan.
        return values + [values[0] - values[-1], values[1] - values[3]]

    def rates(self):
        if len(self.rows) < 2:
            raise ValueError("Need two observations")
        a, b = list(self.rows)[-2:]
        dt = b["t"] - a["t"]
        return [(y - x) / dt for x, y in zip(a["high"], b["high"], strict=True)]


def windows(runs, horizon):
    """Create labels only within a run, after the caller splits whole runs."""
    x, y, persistence, trend, families = [], [], [], [], []
    for run in runs:
        history = History()
        rows = run["observations"]
        for i, row in enumerate(rows):
            history.append(row)
            future = next((r for r in rows[i + 1 :] if r["t"] >= row["t"] + horizon), None)
            if future is None or future["t"] - row["t"] > horizon + 2:
                continue
            # Reject labels across telemetry gaps, run changes or counter resets.
            segment = [r for r in rows[i:] if r["t"] <= future["t"]]
            if any(
                b["t"] - a["t"] > 5
                or b["run_id"] != a["run_id"]
                or any(v < u for u, v in zip(a["high"], b["high"], strict=True))
                for a, b in zip(segment, segment[1:], strict=False)
            ):
                continue
            for p in range(PARTITIONS):
                try:
                    features = history.features(p)
                except ValueError:
                    continue
                dt = future["t"] - row["t"]
                target = (future["high"][p] - row["high"][p]) * horizon / dt
                x.append(features)
                y.append(target)
                persistence.append(features[0] * horizon)
                trend.append(max(0, (features[0] + features[-2]) * horizon))
                families.append(run["family"])
    return np.asarray(x), np.asarray(y), persistence, trend, families
