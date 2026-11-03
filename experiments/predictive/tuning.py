"""Twelve policy candidates: default, four Sobol initialization, seven GP trials."""

import json

from experiments.predictive.runtime import install
from streamscale.predictive import DEFAULT_POLICY
from streamscale.predictive.model import publish


def select(rows):
    feasible = [r for r in rows if r["correct"] and r["p95"] <= 2]
    if not feasible:
        return dict(DEFAULT_POLICY), {"status": "infeasible", "candidates": rows}
    best = min(feasible, key=lambda r: r["replica_seconds"])
    return best["policy"], {"status": "feasible", "selected": best["index"], "candidates": rows}


def optimizer():
    from ax.service.ax_client import AxClient
    from ax.service.utils.instantiation import ObjectiveProperties

    client = AxClient(random_seed=42, verbose_logging=False)
    client.create_experiment(
        name="streamscale-predictive",
        parameters=[
            {"name": "horizon", "type": "choice", "values": [10, 20, 30], "is_ordered": True},
            {"name": "quantile", "type": "choice", "values": [0.8, 0.9, 0.95], "is_ordered": True},
            {"name": "headroom", "type": "range", "bounds": [0.0, 0.3]},
            {"name": "downscale", "type": "range", "bounds": [30, 120], "value_type": "int"},
        ],
        objectives={"replica_seconds": ObjectiveProperties(minimize=True)},
        outcome_constraints=["p95 <= 2.0"],
        choose_generation_strategy_kwargs={"num_initialization_trials": 5},
    )
    return client


async def tune(console, directory, models, metadata, base):
    from experiments.predictive.campaign import config, retained, save, summary

    checkpoint = directory / "optimizer.json"
    if checkpoint.exists():
        from ax.service.ax_client import AxClient

        state = json.loads(checkpoint.read_text())
        client = AxClient.from_json_snapshot(state["ax"])
        rows = state["rows"]
        pending = state["pending"]
    else:
        client = optimizer()
        client.attach_trial(parameters=DEFAULT_POLICY)
        rows, pending = [], {"index": 0, "policy": dict(DEFAULT_POLICY)}
    for ordinal in range(len(rows), 12):
        if pending is None:
            policy, index = client.get_next_trial()
            pending = {"index": index, "policy": policy}
        index, policy = pending["index"], pending["policy"]
        save(checkpoint, {"ax": client.to_json_snapshot(), "rows": rows, "pending": pending})
        candidate = directory / "candidate"
        publish(candidate, models, metadata | {"policy": policy})
        install(candidate)
        scores = []
        for family, seed in (("ramp", 30001), ("repeated-burst", 30002)):
            run = await retained(
                console,
                directory / "tuning",
                f"{ordinal}-{family}",
                config(family, seed, base),
                "ml-predictive",
            )
            score = summary(run)
            if not score["performance_eligible"]:
                raise RuntimeError(
                    "Tuning candidate produced no ML forecasts; inspect retained telemetry"
                )
            scores.append(score)
        row = {
            "index": index,
            "policy": policy,
            "replica_seconds": sum(s["replica_seconds"] for s in scores),
            "p95": max(s["p95"] for s in scores),
            "correct": all(s["correct"] for s in scores),
        }
        # Ax sees only correctness-passing measurements, never failed pipeline executions.
        client.complete_trial(
            index, raw_data={"replica_seconds": row["replica_seconds"], "p95": row["p95"]}
        )
        rows.append(row)
        pending = None
        save(checkpoint, {"ax": client.to_json_snapshot(), "rows": rows, "pending": None})
    return select(rows)
