import copy

import numpy as np
import pytest

from experiments.analyze_predictive import (
    counterfactual_history,
    issued_forecasts,
    metrics,
    paired_windows,
)
from streamscale.topics import PARTITIONS


def trace():
    rows = list(counterfactual_history([120] * 101).rows)
    return {"observations": rows, "family": "steady"}


def test_diagnostic_windows_use_production_labels_and_actual_origins():
    run = trace()
    origins, features, actual = paired_windows(run, 20)
    assert origins[0] == 60
    assert origins[-1] == 80
    assert len(features) == len(origins) * PARTITIONS
    assert np.all(actual == 200)


def test_future_metric_gaps_are_excluded_and_nonmonotonic_rows_rejected():
    run = trace()
    run["observations"] = [r for r in run["observations"] if not 65 <= r["t"] <= 75]
    origins, _, _ = paired_windows(run, 20)
    assert not len(origins)
    run = trace()
    run["observations"][70]["t"] = 60
    with pytest.raises(ValueError, match="increasing"):
        paired_windows(run, 20)


def test_forecasts_are_counted_once_and_aligned_to_future_horizon():
    run = trace()
    p = {"model_id": "local", "forecast_from": 60, "horizon": 20, "forecast_rate": 140}
    run["points"] = [{"predictive": p}, {"predictive": copy.deepcopy(p)}]
    result = issued_forecasts(run)
    assert len(result) == 1
    assert result[0]["actual_horizon_rate"] == 120
    run["observations"] = [r for r in run["observations"] if not 65 <= r["t"] <= 75]
    assert not issued_forecasts(run)


def test_past_probes_have_integer_offsets_and_correct_trend_direction():
    history = counterfactual_history([12] * 71 + [120] * 10)
    assert all(type(n) is int for r in history.rows for n in r["high"])
    assert history.features(0)[-2] > 0
    assert history.features(0)[-1] > 0


def test_coverage_reports_ties_without_claiming_exact_nominal_probability():
    result = metrics(np.array([0, 0, 10]), np.array([0, 1, 5]), 0.9, 10)
    assert result["coverage_including_ties"] == 2 / 3
    assert result["mean_rate_bias"] == pytest.approx(-4 / 30)
    assert metrics(np.array([]), np.array([]), 0.9, 10) is None
