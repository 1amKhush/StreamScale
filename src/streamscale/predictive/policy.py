"""Conservative partition projection with polynomial cost, not n**partitions.

The consumer explicitly uses the range assignor for its single input topic.
A member receives floor(P/n) or ceil(P/n) partitions. After a rebalance, bound
partition service with the largest group and test the hottest group for saturation.
Use actual groups for a stable current assignment. Queue delay is a control proxy,
not a guarantee about reconciled p95 latency.
"""

import math

from streamscale.predictive import DEFAULT_POLICY
from streamscale.topics import MAX_REPLICAS, PARTITIONS


def services(capacity, replicas, assignments=None):
    if assignments is not None:
        if (
            len(assignments) != replicas
            or any(not group for group in assignments)
            or sorted(p for group in assignments for p in group) != list(range(PARTITIONS))
        ):
            raise ValueError("Incomplete partition assignments")
        result = [0.0] * PARTITIONS
        for group in assignments:
            for p in group:
                result[p] = capacity / replicas / len(group)
        return result
    return [capacity / replicas / math.ceil(PARTITIONS / replicas)] * PARTITIONS


def project(lag, rates, capacity, replicas, seconds, assignments=None):
    service = services(capacity, replicas, assignments)
    groups = assignments or [
        sorted(range(PARTITIONS), key=lambda p: rates[p], reverse=True)[
            : math.ceil(PARTITIONS / replicas)
        ]
    ]
    saturated = any(sum(rates[p] for p in group) >= capacity / replicas for group in groups)
    future = [max(0, q + (r - s) * seconds) for q, r, s in zip(lag, rates, service, strict=True)]
    delay = max(q / s for q, s in zip(future, service, strict=True))
    # Keep a finite delay so infeasible candidates can still be ranked. Replacing
    # every saturated candidate with the same sentinel would always select the
    # largest count, even when extra consumers reduce measured throughput.
    return delay, saturated


def recommend(
    row, rates, calibration, policy=None, forecast=None, target=2, minimum=1, maximum=MAX_REPLICAS
):
    if not 1 <= minimum <= maximum <= MAX_REPLICAS:
        raise ValueError("Replica bounds exceed partition capacity")
    if len(rates) != PARTITIONS or len(row["lag"]) != PARTITIONS:
        raise ValueError("Incomplete partition observations")
    policy = policy or DEFAULT_POLICY
    horizon = policy["horizon"]
    demand = [v / horizon for v in forecast["upper"]] if forecast else rates
    demand = [max(a, b) for a, b in zip(demand, rates, strict=True)]
    capacities = {int(k): v for k, v in calibration["capacity"].items()}
    if any(
        n not in capacities or not math.isfinite(capacities[n]) or capacities[n] <= 0
        for n in range(1, MAX_REPLICAS + 1)
    ):
        raise ValueError("Prepare measured capacity for every replica count from 1 to 12")
    current = row["ready"]
    if not 0 <= current <= MAX_REPLICAS:
        raise ValueError("Ready replica count outside capacity bounds")
    old_service = (
        services(capacities[current] * (1 - policy["headroom"]), current, row["assignments"])
        if current and row["stable"]
        else [0.0] * PARTITIONS
    )
    scores = []
    for n in range(minimum, maximum + 1):
        capacity = capacities[n] * (1 - policy["headroom"])
        startup = calibration["actuation_seconds"] if n != row["desired"] else 0
        after_start = [
            max(0, q + (r - s) * startup)
            for q, r, s in zip(row["lag"], demand, old_service, strict=True)
        ]
        assignment = row["assignments"] if n == current and row["stable"] else None
        score, saturated = project(after_start, demand, capacity, n, horizon, assignment)
        score += calibration.get("handling_seconds", 0)
        scores.append(
            {
                "replicas": n,
                "latency_proxy_s": score,
                "saturated": saturated,
                "feasible": score <= target and not saturated,
            }
        )
    feasible = [s for s in scores if s["feasible"]]
    chosen = (
        feasible[0]
        if feasible
        else min(scores, key=lambda s: (s["latency_proxy_s"], s["replicas"]))
    )
    return {
        "recommended": chosen["replicas"],
        "capacity_limited": not feasible,
        "candidates": scores,
        "reason": (
            f"{chosen['replicas']} replicas: smallest predicted capacity meeting {target:g}s"
            if feasible
            else f"Latency target infeasible: partition or shared capacity limit; "
            f"{chosen['replicas']} replicas give the lowest projected delay"
        ),
    }
