"""Read-only diagnostics for a frozen predictive artifact and its recorded traces.

Run in the separately locked preparation environment. This never fits a model,
selects a policy, installs artifacts or contacts the cluster.
"""

import argparse
import bisect
import csv
import json
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import mean_pinball_loss
from threadpoolctl import threadpool_limits

from streamscale.predictive import HORIZONS, QUANTILES
from streamscale.predictive.data import History, windows
from streamscale.predictive.model import digest, load
from streamscale.predictive.policy import recommend
from streamscale.topics import PARTITIONS

FEATURES = ("rate_5s", "rate_10s", "rate_20s", "rate_30s", "rate_60s", "trend_5_60", "trend_10_30")


def paired_windows(run, horizon):
    """Attach timestamps to production windows, checking exact feature/label parity."""
    rows = run["observations"]
    times = [r["t"] for r in rows]
    if any(b <= a for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("Analysis requires strictly increasing observation timestamps")
    origins, x, y = [], [], []
    history = History()
    for i, row in enumerate(rows):
        history.append(row)
        j = bisect.bisect_left(times, row["t"] + horizon, lo=i + 1)
        if j >= len(rows) or rows[j]["t"] - row["t"] > horizon + 2:
            continue
        segment = rows[i : j + 1]
        if any(
            b["t"] - a["t"] > 5
            or b["run_id"] != a["run_id"]
            or any(v < u for u, v in zip(a["high"], b["high"], strict=True))
            for a, b in zip(segment, segment[1:], strict=False)
        ):
            continue
        try:
            features = [history.features(p) for p in range(PARTITIONS)]
        except ValueError:
            continue
        origins.append(row["t"])
        x.extend(features)
        y.extend(
            (rows[j]["high"][p] - row["high"][p]) * horizon / (rows[j]["t"] - row["t"])
            for p in range(PARTITIONS)
        )
    px, py, *_ = windows([run], horizon)
    assert np.array_equal(np.asarray(x), px), "Analysis and production features differ"
    assert np.array_equal(np.asarray(y), py), "Analysis and production horizon labels differ"
    return np.asarray(origins), px, py


def publication_end(run):
    return min(
        (p["t"] for p in run["points"] if (p.get("sale") or {}).get("state") == "finished"),
        default=float("inf"),
    )


def metrics(actual, prediction, quantile, horizon):
    if not len(actual):
        return None
    return {
        "samples": len(actual),
        "pinball_count": float(mean_pinball_loss(actual, prediction, alpha=quantile)),
        "coverage_including_ties": float(np.mean(actual <= prediction)),
        "mean_actual_rate": float(np.mean(actual) / horizon),
        "mean_predicted_rate": float(np.mean(prediction) / horizon),
        "mean_rate_bias": float(np.mean(prediction - actual) / horizon),
        "rate_mae": float(np.mean(np.abs(prediction - actual)) / horizon),
    }


def issued_forecasts(run):
    """Count one forecast per origin, aligning actuals without filling missing time."""
    observations = run["observations"]
    times = [r["t"] for r in observations]
    seen, result = set(), []
    for point in run["points"]:
        p = point.get("predictive") or {}
        origin, horizon = p.get("forecast_from"), p.get("horizon")
        key = (p.get("model_id"), origin)
        if origin is None or horizon is None or p.get("forecast_rate") is None or key in seen:
            continue
        seen.add(key)
        i = bisect.bisect_left(times, origin - 0.001)
        j = bisect.bisect_left(times, origin + horizon)
        if i >= len(times) or abs(times[i] - origin) > 0.001 or j >= len(times):
            continue
        if times[j] - origin > horizon + 2 or any(
            b["t"] - a["t"] > 5
            or b["run_id"] != a["run_id"]
            or any(y < x for x, y in zip(a["high"], b["high"], strict=True))
            for a, b in zip(observations[i:j], observations[i + 1 : j + 1], strict=True)
        ):
            continue
        actual = sum(
            y - x for x, y in zip(observations[i]["high"], observations[j]["high"], strict=True)
        ) / (times[j] - origin)
        result.append(
            {
                "origin": origin,
                "horizon": horizon,
                "observed_rate": p.get("observed_rate"),
                "forecast_rate": p["forecast_rate"],
                "forecast_median_rate": p.get("forecast_median_rate"),
                "actual_horizon_rate": actual,
                "recommended": p.get("recommended"),
                "ready": p.get("ready"),
                "capacity_limited": p.get("capacity_limited"),
                "reason": p.get("reason"),
            }
        )
    return result


def counterfactual_history(rates):
    """Valid synthetic past offsets; no future trajectory or expected label."""
    history = History()
    offsets = np.zeros(PARTITIONS)
    for second, rate in enumerate(rates):
        offsets += rate / PARTITIONS
        history.append(
            {
                "t": float(second),
                "run_id": "offline-history-probe",
                "high": np.floor(offsets).astype(int).tolist(),
                "position": np.floor(offsets).astype(int).tolist(),
                "lag": [0] * PARTITIONS,
                "ready": 1,
                "desired": 1,
                "stable": True,
                "assignments": [list(range(PARTITIONS))],
            }
        )
    return history


def analyze(campaign, output):
    folder = campaign / ("model" if (campaign / "model/current.json").exists() else "candidate")
    identifier, models, metadata = load(folder)
    training = [json.loads(p.read_text()) for p in sorted(campaign.glob("train-*.json"))]
    validation = [json.loads(p.read_text()) for p in sorted(campaign.glob("validation-*.json"))]
    assert {r["id"] for r in training} == set(metadata["training_ids"])
    assert {r["id"] for r in validation} == set(metadata["validation_ids"])
    assert not set(metadata["training_ids"]) & set(metadata["validation_ids"])
    assert (
        digest(training + validation) == metadata["data_sha256"]
        or digest(
            [next(r for r in training if r["id"] == i) for i in metadata["training_ids"]]
            + [next(r for r in validation if r["id"] == i) for i in metadata["validation_ids"]]
        )
        == metadata["data_sha256"]
    )
    output.mkdir(parents=True, exist_ok=True)
    scores, traces, distributions = [], {}, []
    with threadpool_limits(limits=1):
        for horizon in HORIZONS:
            for run in training:
                origins, _, targets = paired_windows(run, horizon)
                total = targets.reshape(-1, PARTITIONS).sum(axis=1)
                distributions.append(
                    {
                        "split": "training",
                        "session": run["id"],
                        "family": run["family"],
                        "horizon": horizon,
                        "origins": len(origins),
                        "partition_windows": len(targets),
                        "zero_future_fraction": float(np.mean(total == 0)),
                        "post_publication_fraction": float(
                            np.mean(origins >= publication_end(run))
                        ),
                    }
                )
            for run in validation:
                origins, x, actual = paired_windows(run, horizon)
                future = actual.reshape(-1, PARTITIONS).sum(axis=1)
                median = np.maximum(0, models[horizon, 0.5].predict(x))
                active = origins < publication_end(run)
                for quantile in QUANTILES:
                    raw = np.maximum(0, models[horizon, quantile].predict(x))
                    live = np.maximum(median, raw)
                    total = live.reshape(-1, PARTITIONS).sum(axis=1)
                    observed = x[:, 0].reshape(-1, PARTITIONS).sum(axis=1)
                    expected = metadata["scores"][f"{horizon}:{quantile}"][run["family"]]["model"]
                    assert np.isclose(mean_pinball_loss(actual, raw, alpha=quantile), expected)
                    for label, mask in (
                        ("all", np.ones(len(origins), dtype=bool)),
                        ("publishing_origin", active),
                        ("nonzero_future", future > 0),
                        (
                            "future_surge",
                            future / horizon > np.maximum(observed * 1.5, observed + 20),
                        ),
                        ("future_drop", (observed > 10) & (future / horizon < observed * 0.5)),
                        ("post_publication", ~active),
                    ):
                        partmask = np.repeat(mask, PARTITIONS)
                        scores.append(
                            {
                                "family": run["family"],
                                "session": run["id"],
                                "horizon": horizon,
                                "quantile": quantile,
                                "slice": label,
                                "origin_count": int(mask.sum()),
                                "raw_partition": metrics(
                                    actual[partmask], raw[partmask], quantile, horizon
                                ),
                                "live_partition": metrics(
                                    actual[partmask], live[partmask], quantile, horizon
                                ),
                                "aggregate": metrics(future[mask], total[mask], quantile, horizon),
                                "quantile_crossing_fraction": float(
                                    np.mean(raw[partmask] < median[partmask])
                                )
                                if mask.any()
                                else None,
                            }
                        )
                    if horizon == 20 and quantile == 0.9:
                        traces[run["family"]] = {
                            "origin_seconds": (origins - run["observations"][0]["t"]).tolist(),
                            "horizon": horizon,
                            "actual_horizon_rate": (future / horizon).tolist(),
                            "observed_5s_rate": observed.tolist(),
                            "median_rate": (
                                median.reshape(-1, PARTITIONS).sum(axis=1) / horizon
                            ).tolist(),
                            "upper_rate": (total / horizon).tolist(),
                            "publication_end_seconds": publication_end(run)
                            - run["observations"][0]["t"],
                        }
        probes = []
        patterns = {f"constant_{rate}": [rate] * 81 for rate in (0, 34, 100, 300, 600, 1200, 2400)}
        patterns |= {
            "new_surge_2s": [34] * 79 + [1000] * 2,
            "surge_10s": [34] * 71 + [1000] * 10,
            "surge_30s": [34] * 51 + [1000] * 30,
            "burst_ended_5s_ago": [34] * 46 + [1000] * 30 + [34] * 5,
            "rising_60s": [34] * 20 + np.linspace(34, 1000, 61).tolist(),
            "falling_60s": [1000] * 20 + np.linspace(1000, 34, 61).tolist(),
        }
        for name, past in patterns.items():
            history = counterfactual_history(past)
            x = [history.features(p) for p in range(PARTITIONS)]
            median = np.maximum(0, models[20, 0.5].predict(x))
            upper = np.maximum(median, models[20, 0.9].predict(x))
            decision = recommend(
                history.rows[-1],
                history.rates(),
                metadata["calibration"],
                forecast={"upper": upper.tolist()},
            )
            probes.append(
                {
                    "past_pattern": name,
                    "last_observed_rate": sum(history.rates()),
                    "aggregate_features": dict(
                        zip(FEATURES, np.sum(x, axis=0).tolist(), strict=True)
                    ),
                    "median_rate": float(sum(median) / 20),
                    "upper_rate": float(sum(upper) / 20),
                    "default_policy_recommended": decision["recommended"],
                    "capacity_limited": decision["capacity_limited"],
                }
            )
    tuning = []
    for p in sorted((campaign / "tuning").glob("*.json")):
        run = json.loads(p.read_text())
        issued = issued_forecasts(run)
        intervals = [
            (a, b["t"] - a["t"]) for a, b in zip(run["points"], run["points"][1:], strict=False)
        ]
        known = sum(dt for _, dt in intervals if 0 < dt <= 5)
        gaps = sum(dt for _, dt in intervals if dt > 5)
        status_seconds = Counter()
        for point, dt in intervals:
            if 0 < dt <= 5:
                status_seconds[(point.get("predictive") or {}).get("status", "unknown")] += dt
        tuning.append(
            {
                "capture": p.name,
                "session": run["id"],
                "policy": next(
                    (
                        p["predictive"]["policy"]
                        for p in run["points"]
                        if (p.get("predictive") or {}).get("policy")
                    ),
                    None,
                ),
                "aligned_unique_forecasts": len(issued),
                "issued": issued,
                "known_interval_seconds": known,
                "gap_seconds": gaps,
                "status_seconds": dict(status_seconds),
                "ready_counts": sorted(
                    {p["ready"] for p in run["points"] if p.get("ready") is not None}
                ),
                "p95": run["record"]["reconciliation"]["latency_s"]["p95"],
            }
        )
    result = {
        "scope": (
            "Read-only analysis of frozen observed-history models. Candidate analysis is "
            "not final policy or held-out performance evidence."
        ),
        "campaign": str(campaign),
        "artifact_kind": folder.name,
        "model_id": identifier,
        "model_sha256": metadata["model_sha256"],
        "data_sha256": metadata["data_sha256"],
        "feature_names": FEATURES,
        "training_ids": metadata["training_ids"],
        "validation_ids": metadata["validation_ids"],
        "capacity": metadata["calibration"],
        "validation": scores,
        "training_distribution": distributions,
        "validation_traces": traces,
        "past_only_probes": probes,
        "tuning": tuning,
    }
    (output / "analysis.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    with (output / "past-only-probes.csv").open("w", newline="") as stream:
        rows = [{k: v for k, v in row.items() if k != "aggregate_features"} for row in probes]
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "model_id": identifier,
                "validation_slices": len(scores),
                "tuning_captures": len(tuning),
                "output": str(output),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    campaign = args.campaign
    if campaign is None:
        pointer = Path("artifacts/predictive/current.json")
        if not pointer.exists():
            raise SystemExit("Run ./streamscale.sh predictive-prepare before predictive-analyze")
        campaign = pointer.parent / json.loads(pointer.read_text())["fingerprint"]
    analyze(campaign, args.output or campaign / "analysis")


if __name__ == "__main__":
    main()
