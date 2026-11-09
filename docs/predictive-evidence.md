# ML Predictive implementation evidence

M16–M20 extend the original scope with one history-only scaling mode. Acceptance
results below distinguish automated checks from live experimental evidence.

## M16 — data and model

Shared allowlisted observation extraction, contiguous history features, whole-run
splits, direct horizon labels, quantile HGBT models, family-specific pinball loss
against persistence/trend, and atomic checksummed local artifacts are implemented.
Live traces and empirical capacity calibration are collected by the M18 workflow.

## M17 — controller

Five-second discrete policy evaluation, calibrated capacity by replica count,
worst-case partition assignment projection, delayed actuation, stabilization,
manual suspension and exclusive actuator locking are implemented. Missing model
calibration holds replicas; fresh observations with a loaded model use reactive
fallback during warm-up. Unit tests exercise stale data, ownership transitions,
override/release, warm-up scaling and suppression of duplicate writes.

## M18 — preparation

A separately locked CPU-only Ax/BoTorch environment drives twelve policy trials
(default plus four initialization trials, then GP suggestions). Every trial uses
the same two live tuning workloads and exact reconciled SQL p95, never simulated
objective values. Failed correctness aborts preparation. Completed captures and
Ax recommendations are checkpointed. Training, validation, tuning and evaluation
use disjoint seeds/runs. Environment fingerprints include source, runtime versions,
consumer resources and node information. The accepted artifact is published only
after tuning; infeasible tuning retains the conservative default explicitly.

## M19 — console

One additional ML Predictive mode, inline decision/status, latency target, model
identity and a dashed forecast on the existing flow chart are implemented.
Forecasts are horizon means plotted at interval end and compared against actual
observations over that same interval; future observations remain unknown. CSV
and replay preserve the model, status, forecast and decision reason. Manual
control and the existing comparison view are reused.

No SQL migration: `experiment_run.mode` retains its legacy three-value constraint.
Predictive sessions use its `fixed` storage category, as manual sessions already
do. Run JSON, `configuration.experiment_configuration.mode`, console start mode,
metrics and session configuration retain the actual `ml-predictive` identity;
predictive comparisons use session/configuration identity, never the SQL category.

## M20 — validation status

Implemented maintained launcher/Make commands, the separate resumable 36-run
campaign, a three-minute representative UI preset, live forecast/override/fault
acceptance and browser acceptance. The historical M7 campaign is unchanged.

Validated on 2026-11-03 during implementation:

- 176 pipeline/unit checks passed, including scoped launcher command tests.
- 144 console unit checks passed (live checks deselected).
- 20 web checks and the TypeScript/production build passed.
- Ax CPU-only Sobol and GP generation exercised with an explicitly synthetic
  objective as an API smoke check; this is not live tuning/performance evidence.
- Ruff lint/format checks passed before the final validation run.

The complete updated launcher was started with its output retained in
`artifacts/predictive-full-launcher.log`. Live results are recorded separately
below when available. A first full campaign requires at least 5 GiB free for
retained raw evidence, Kafka and SQL growth. Preparation fails before starting
new captures if space is insufficient; it never deletes project data.

### Live validation outcome

The full launcher passed 176 unit checks and all 28 local integration checks,
then stopped at the existing `previous_drained` cluster guard. The retained
2026-10-12 measured consumer reports `Captured source records are no longer
available` and Kafka offset-out-of-range errors after retention. The guard and
all retained evidence were preserved; this execution is not marked reconciled.

The current console worker was independently built/deployed without replacing
that historical measured execution. All console comparison modes share this
current base and unchanged resource limits. A real 30-second missing-model
fallback sale reconciled successfully: exact SQL p95 1.073092s, no correctness
errors, and manual override/release verified. Its scope is explicitly fallback,
not trained predictive performance (`artifacts/predictive/fallback-live-check.json`).

The live model preparation preflight was attempted separately. Storage is below
its 5 GiB admission requirement. Full training/tuning, trained-model browser
acceptance, held-out 36-run evaluation and live complete-launcher reuse remain
unexecuted; no performance improvement or M16–M20 live acceptance is claimed.
Scoped tests exercise the entire launcher order, failure propagation and reuse
command routing without stopping user services.

A real Chrome check of the single selector, editable latency target and 390px
mobile layout passed (1 Playwright check); no browser errors or horizontal
page overflow. Screenshot: `artifacts/console/predictive-mobile.png`. The trained
sale/forecast/recovery/browser journey is a separate check and remains pending.
Preparation's actual preflight failure reported 1.3 GiB free versus 5 GiB required
(`artifacts/predictive-prepare-check.log`). No model or tuned policy was promoted.

## Twelve-partition follow-up (2026-11-09)

The earlier low-disk outcome above describes the original three-partition attempt.
M21 expands the maintained configuration to twelve partitions and 1–12 replicas.
Live calibration at all twelve counts and the twelve-consumer correctness check
have now completed. The wider range exposes capacity plateaus as well as scaling
behavior; it does not establish a performance improvement. Training, offline
tuning and the final held-out comparison are in progress. See the evolving
[M21 evidence](capacity-evidence.md) and [measured capacity report](results/capacity-12/report.md).
