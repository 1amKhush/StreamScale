"""Synthetic Ax API acceptance; no workloads, training traces or cluster writes."""

import importlib.metadata
import json

from experiments.predictive.tuning import optimizer
from streamscale.predictive import DEFAULT_POLICY


def main():
    client = optimizer()
    _, index = client.attach_trial(parameters=DEFAULT_POLICY)
    client.complete_trial(index, raw_data={"replica_seconds": 100.0, "p95": 3.0})
    sequence = ["manual-default"]
    for _ in range(5):
        _, index = client.get_next_trial()
        sequence.append(client.experiment.trials[index].generator_run._model_key)
        if index < 5:
            client.complete_trial(
                index, raw_data={"replica_seconds": float(100 + index), "p95": 3 + index / 10}
            )
    expected = ["manual-default"] + ["Sobol"] * 4 + ["BoTorch"]
    if sequence != expected:
        raise RuntimeError(f"Ax initialization budget mismatch: {sequence}; expected {expected}")
    print(
        json.dumps(
            {
                "status": "passed",
                "scope": "Synthetic Ax API/order check only; not live tuning evidence",
                "ax_version": importlib.metadata.version("ax-platform"),
                "sequence": sequence,
                "first_gp_trial_index": index,
                "live_candidate_budget": 12,
                "live_gp_candidates": 7,
            }
        )
    )


if __name__ == "__main__":
    main()
