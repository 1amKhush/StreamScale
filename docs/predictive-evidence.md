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
