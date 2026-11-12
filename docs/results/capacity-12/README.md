# Twelve-partition evidence index

Latest supplied run, evaluated on 2026-11-12:

- [Reviewed digest](sim-run-2026-11-12/README.md), with source hashes, grouped
  metrics, conditional forecast scores, tuning candidates and fresh plots.
- [Current model analysis](../../predictive-model-analysis.md), describing the
  predictor, controller, likely causes and limits of the evidence.
- [Acceptance record](acceptance.json), separating directly audited summaries
  from the source narrative's broader acceptance claims.

[report.md](report.md) and [capacity-evidence.md](../../capacity-evidence.md) keep
the earlier qualification chronology plus the latest addendum. The final project
report is still to be prepared.

The older model-analysis folders, `forecast-validation.json`, calibration and
CPU-profile files are preserved measurements from earlier models/environments.
In particular, `calibration-current.*` is explicitly dated 2026-11-10 inside its
JSON and retains its original filename for historical links; it is not the
2026-11-12 supplied capacity curve. Use the latest digest's `calibration` field
for the new run. Shutdown/cleanup records describe their original dated actions,
not the current state of a service on another run machine.
