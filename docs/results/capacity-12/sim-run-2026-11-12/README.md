# Reviewed digest of the supplied run

This is an evidence digest and analysis input, updated 2026-11-12. It is not the
final project report. Original supplied artifacts remain under [`sim_run/`](../../../../sim_run/README.md).

- [Summary and source SHA256 hashes](summary.json): 36 unique complete rows,
  correctness/performance flags, model/runtime identity, disjoint recorded split
  IDs, environment, calibration and evidence limits.
- [Outcomes](outcomes.csv): means/ranges by workload and mode. Mean p95 is the
  mean of three per-run p95 values, not a pooled percentile.
- [Forecast validation](forecast-validation-20s-q90.csv): coverage and bias by
  workload family and conditional slice at the default horizon/quantile.
- [Tuning candidates](tuning-candidates.csv): twelve policies, all correctness
  passing and all infeasible for the two-second constraint across both workloads.
- [Policy probe calculation](policy-probe-projection.json): explains why the
  synthetic zero-traffic probe can be transition-limited from one ready replica.
  These scores are control proxies, not measured latency or new experiments.
- [Latency/resource plot](figures/heldout-tradeoff.svg),
  [forecast interval plot](figures/traffic-forecasts.svg) and
  [coverage/sensitivity plot](figures/forecast-calibration.svg), with PNG companions,
  are regenerated from supplied numeric records.

The supplied narrative reports all milestones passing, including 45 live and
20 browser checks. This bundle directly supports the row-level campaign and model
analysis, but does not contain their raw test logs, the separate capacity-check
record, model pickle, full campaign captures or launcher-reuse log. Those claims
remain attributed to the source report rather than independently re-executed.
The runtime model pointer is not changed by importing these summaries.

See the [model analysis](../../../predictive-model-analysis.md) for workings,
likely causes, limitations and follow-up experiments.
