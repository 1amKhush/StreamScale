"""Exact identity/value reconciliation; no assumptions about the business schema."""

import math
from collections import Counter
from datetime import datetime


def reconcile(records, acknowledgements, rows, expected):
    by_id = {r["id"]: r for r in records}
    accepted = {ack["id"]: ack for ack in acknowledgements if not ack.get("error")}
    wanted = {identity: expected(by_id[identity]["payload"]) for identity in accepted}
    counts = Counter(str(row["id"]) for row in rows)
    actual = {str(row["id"]): row for row in rows}
    missing = sorted(wanted.keys() - actual.keys())
    unexpected = sorted(actual.keys() - wanted.keys())
    mismatched = sorted(i for i in wanted.keys() & actual.keys() if actual[i]["value"] != wanted[i])
    duplicates = sorted(i for i, count in counts.items() if count > 1)
    latencies = []
    invalid_timing = []
    for identity in wanted.keys() & actual.keys():
        try:
            start = datetime.fromisoformat(by_id[identity]["produced_at"])
            end = datetime.fromisoformat(actual[identity]["completed_at"])
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("Timezone required")
            seconds = (end - start).total_seconds()
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Invalid clock")
            latencies.append(seconds)
        except (KeyError, TypeError, ValueError):
            invalid_timing.append(identity)
    return {
        "missing": missing,
        "unexpected": unexpected,
        "mismatched": mismatched,
        "duplicates": duplicates,
        "invalid_timing": sorted(invalid_timing),
        "expected": len(wanted),
        "observed": len(rows),
        "p95_seconds": sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
        if latencies and not invalid_timing and not missing
        else None,
        "correct": not (missing or unexpected or mismatched or duplicates),
    }
