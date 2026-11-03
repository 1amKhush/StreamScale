# ML Predictive

One scaling mode uses 60 seconds of observed Kafka arrivals to forecast 10, 20
or 30 seconds ahead. Twelve quantile HGBT models predict per-partition counts
(median, 80th, 90th and 95th percentile for each horizon). The controller evaluates
1–3 replicas every five seconds, using empirical capacity at each count and
partition assignment constraints. It minimizes capacity while aiming for p95
latency ≤ 2 seconds. Its queue-delay estimate is a control proxy, not an exact
p95 guarantee. DB failures, skew and unprecedented jumps can defeat that target.

`predictive-prepare` calibrates capacity, collects live training and validation
runs, reports family-specific pinball loss against persistence/trend baselines,
and evaluates twelve policy candidates on two separate live tuning workloads.
Ax uses the default plus four initialization trials before GP-based suggestions.
The objective is replica-seconds subject to reconciled SQL p95 ≤ 2 seconds;
correctness failures abort the campaign. If no candidate is feasible, the default
20s/.90/15%/60s policy remains and infeasibility is recorded.

No future schedules, scenario names, seeds or absolute sale clocks enter model
features. Training uses whole-run splits before window extraction. The separate
36-run evaluation never updates the model or policy. Source/dependency/resource/
node fingerprints select immutable campaign directories; completed captures are
reused and unfinished captures remain in console session history. The Ax state,
selected candidate and finished scores are checkpointed atomically together.

The agent owns the loop, even without a browser. It serializes writes with mode
transitions and manual overrides, checks for unexpected HPA/KEDA ownership, and
waits for ready pods and stable assignments before issuing another action.
Fresh lag and measured capacity provide reactive behavior during history warm-up
or prediction failure. Missing calibration or stale data hold replicas. A new
run or agent restart requires fresh history. Model integrity, runtime versions
and feature code are checked before loading locally prepared artifacts.

In the UI choose **ML Predictive**, then **ML Predictive pilot**. The existing
flow chart displays forecast horizon means at their future interval ends and
compares against observed means over those same intervals. Unknown observations
remain gaps. The latency target is the primary setting; tuning stays offline.
Status, decision reason, model/policy identity and forecasts remain in session
telemetry and exports for replay.

No SQL migration is required: the legacy `experiment_run.mode` category is fixed
for predictive sessions. JSON configuration/session identity and metrics retain
`ml-predictive`. SQL users should read `configuration->'experiment_configuration'
->>'mode'` for the actual policy. Historical M7 evidence is preserved separately.

See [run commands](run-project.md#ml-predictive-m16m20) and
[validation evidence](predictive-evidence.md).
