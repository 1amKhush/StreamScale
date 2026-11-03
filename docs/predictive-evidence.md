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
