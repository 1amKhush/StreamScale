"""Three repetitions are required and missing responses must remain missing."""

import pytest

from experiments.analyze import METRICS, aggregate, display
from experiments.evidence import MODES, SCENARIOS


def rows():
    return [
        {
            "mode": mode,
            "scenario": scenario,
            "repetition": repeat,
            **{metric: repeat for metric in METRICS},
        }
        for mode in MODES
        for scenario in SCENARIOS
        for repeat in range(1, 4)
    ]


def test_descriptive_aggregation_uses_per_run_values_and_reports_range():
    result = aggregate(rows())
    row = result[0]
    assert row["observed"] == 3
    assert row["median"] == 2
    assert (row["minimum"], row["maximum"]) == (1, 3)
    assert display(row) == "2.00 [1.00, 3.00]"


def test_missing_scaling_response_is_not_a_zero_second_response():
    source = rows()
    for row in source:
        row["scaling_response_seconds"] = None
    summaries = [row for row in aggregate(source) if row["metric"] == "scaling_response_seconds"]
    assert all(row["median"] is None and row["missing"] == 3 for row in summaries)
    assert display(summaries[0]) == "unobserved"


def test_incomplete_repetitions_cannot_be_ranked():
    with pytest.raises(RuntimeError, match="three distinct"):
        aggregate(rows()[1:])
