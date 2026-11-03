"""Small discrete MPC approximation; queue delay is a proxy, not an SLO guarantee."""

import itertools

from streamscale.predictive import DEFAULT_POLICY


def groups(replicas, current=None):
    if current and len(current) == replicas and sorted(sum(current, [])) == [0, 1, 2]:
        return [current]
    # Worst feasible partition assignment after a rebalance, rather than equal traffic.
    result = []
    for owners in itertools.product(range(replicas), repeat=3):
        if set(owners) == set(range(replicas)):
            result.append([[p for p in range(3) if owners[p] == n] for n in range(replicas)])
    return result


def delay(lag, rates, capacity, replicas, assignments=None):
    per_member = capacity / replicas
    worst = 0.0
    for assignment in groups(replicas, assignments):
        for partitions in assignment:
            service = per_member / len(partitions)
            worst = max(worst, *(q / service for p, q in enumerate(lag) if p in partitions))
            if sum(rates[p] for p in partitions) >= per_member:
                worst = max(worst, 1e6)
    return worst


def recommend(row, rates, calibration, policy=None, forecast=None, target=2, minimum=1, maximum=3):
    policy = policy or DEFAULT_POLICY
    horizon = policy["horizon"]
    demand = [v / horizon for v in forecast["upper"]] if forecast else rates
    # The current observed rate protects against a forecast missing a sudden onset.
    demand = [max(a, b) for a, b in zip(demand, rates, strict=True)]
    capacities = {int(k): v for k, v in calibration["capacity"].items()}
    current = max(1, row["ready"])
    old_capacity = capacities[current] * (1 - policy["headroom"])
    scores = []
    for n in range(minimum, maximum + 1):
        capacity = capacities[n] * (1 - policy["headroom"])
        startup = calibration["actuation_seconds"] if n != row["desired"] else 0
        # Existing capacity remains during startup; do not credit new pods early.
        after_start = [
            max(0, q + (r - old_capacity / 3) * startup)
            for q, r in zip(row["lag"], demand, strict=True)
        ]
        # Project each possible assignment and take its worst partition outcome.
        score = 0.0
        for assignment in groups(n, row["assignments"] if n == current else None):
            future = list(after_start)
            for partitions in assignment:
                service = capacity / n / len(partitions)
                for p in partitions:
                    future[p] = max(0, future[p] + (demand[p] - service) * horizon)
            score = max(score, delay(future, demand, capacity, n, assignment))
        score += calibration.get("handling_seconds", 0)
        scores.append({"replicas": n, "latency_proxy_s": score, "feasible": score <= target})
    feasible = [s for s in scores if s["feasible"]]
    chosen = (
        feasible[0]
        if feasible
        else min(scores, key=lambda s: (s["latency_proxy_s"], -s["replicas"]))
    )
    return {
        "recommended": chosen["replicas"],
        "capacity_limited": not feasible,
        "candidates": scores,
        "reason": (
            f"{chosen['replicas']} replicas: smallest predicted capacity meeting {target:g}s"
            if feasible
            else "Latency target infeasible: partition or shared capacity limit"
        ),
    }
