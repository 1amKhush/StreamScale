# Supplied complete-run artifacts

Added by the user for the run evaluated on 2026-11-12 at commit `7e01206`.
The original supplied files are preserved. This directory is an imported evidence
bundle; it is not a model installation directory or a resumable campaign checkpoint.

- `summary.csv` and `report.md`: 36 held-out summary rows for three workloads,
  four scaling modes and three repetitions. All rows report correctness and
  complete measurement; 15 meet p95 ≤ 2 seconds.
- `analysis.json` and `past-only-probes.csv`: validation and past-history
  sensitivity diagnostics for the frozen traffic model.
- The long hexadecimal `.json` filename: model metadata, capacity calibration,
  source/environment identity and twelve policy-candidate scores. Tuning is
  explicitly **infeasible**, so the conservative default policy was retained.
- `StreamScale_Comprehensive_Evaluation_Report.md` and its metadata: the supplied
  narrative report. Its local-machine image paths are not portable. Claims of an
  optimal tuned policy, uniformly calibrated/monotonic forecasts and fastest ML
  recovery are corrected in the reviewed analysis below. Its test/capacity-check
  declarations do not have accompanying raw logs in this bundle.
- JPEGs: supplied figures, including duplicate exports and historical three-mode
  benchmark plots. Keep their provenance separate from the four-mode CSV.

See the [updated model analysis](../docs/predictive-model-analysis.md),
[audited machine-readable digest](../docs/results/capacity-12/sim-run-2026-11-12/summary.json)
and [derived tables/figures](../docs/results/capacity-12/sim-run-2026-11-12/README.md).
The final project report will be prepared separately.
