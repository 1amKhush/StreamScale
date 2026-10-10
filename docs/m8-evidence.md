# M8 and complete-project acceptance

M8 reporting tools started at 2026-10-08T11:49:56Z while M7 collected its frozen
matrix. Final report generation required the completed, independently audited
27-run campaign. The complete M0–M8 launcher was observed passing at
2026-10-08T13:13:40Z; all local milestones are now complete.

Actual final acceptance command:

```sh
./streamscale.sh all --no-ui
```

It exited **0**. `artifacts/m8-full-check.log` records 144 passing unit tests,
28 passing live integration tests (including outages/crash/rebalance behavior),
clean Ruff checks and 52 formatted Python files, local smoke/fixture checks,
fresh cluster SQL/Prometheus/Grafana verification, the three independent M6
mode exercises, the complete retained M7 audit and M8 report regeneration.
The launcher file was left unchanged during this execution, resolving the
older in-flight shell's file-offset issue recorded in M7 evidence.

The final scaling checks each stored 5,940 unique facts and reconciled without
gaps or state regression. Fixed peaked at one ready consumer; CPU-HPA and
lag-KEDA peaked at three, with controller isolation and 1–3 bounds verified.
Their run UUIDs are respectively `65b3155e-9ae7-47ca-ac74-4f21b749a3bc`,
`31764623-894b-4dd7-b166-c5cbc5812cc9` and
`ebcfa88a-b3d2-4af4-bf0c-61a1db14f865`. These fresh functional checks used
`streamscale:a0363a80dfc6f511`, Git `1d2b70a`, clean build; they are separate
from the original immutable M7 measurement image documented in its report.

M7 reuse validated the same 56 accepted executions, 27 measured cells,
3,818 raw samples and original consumer image/resource controls. It did not
remeasure or fabricate the matrix. File hashes, comparison CSV values, SQL
p95/window throughput, fresh groups and logical scenario identity all passed.
Report regeneration produced byte-identical tracked artifacts in this environment.

`experiments/analyze.py` reads audited evidence and generates three standalone
SVG/PNG figures, a four-page PDF, an interpretation report, aggregate CSV and
provenance. Its own Matplotlib 3.11.2 environment is locked separately under
`experiments/analysis/`, preserving the measured consumer dependencies. The
consumer Docker context excludes host experiment tooling. Reporting refuses
incomplete evidence; tests cover three distinct repetitions and missing
responses remaining missing. A selected relative campaign path also succeeded:

```sh
uv run --project experiments/analysis --frozen python -m experiments.analyze \
  --campaign artifacts/experiments/campaign-20261008T113717-328739
```

Primary and resource charts show each repetition, median and observed range.
Trajectories show every run's broker lag and ready consumers. The real charts
and PDF cover were visually inspected; plot headroom was corrected so markers
and whiskers are not clipped. `pdfinfo` confirmed four A4 landscape pages.
The report labels the base measurement window correctly: shared late fixtures
extend actual publication to about 70 seconds while the primary window is
60 seconds. It explicitly explains the tail's effect on stable-zero recovery.

Observed median run p95 values for fixed / CPU-HPA / lag-KEDA were 37.81 /
20.35 / 4.49 seconds in flash sale and 67.34 / 24.84 / 8.95 seconds in overload.
Steady medians were all about 0.70 seconds with one ready consumer. KEDA's
lower burst latency came with higher median replica-seconds: 173 versus 90 for
fixed in flash sale and 190 versus 120 in overload. These are descriptive
local observations with three repetitions, not confidence intervals or a
production improvement claim. The report covers shared-node resources, cache
and retained-data carryover, controller cadence, sampling/coverage, delayed
fixtures, short windows and at-least-once replay/DLQ limits.

The launcher leaves the fixed demo `run-20261008131220-59df711a` running,
UUID `427bb2f5-e97c-4232-b6f1-03164fe2eedb`, with 396 facts, 200 states, four
DLQ sources and zero lag. Local Compose services, kind services and data remain
available. Normal shutdown/resume scoping was verified with mocked containers;
live user services were not stopped solely to test lifecycle documentation.

Open [the report](results/current/report.md),
[primary outcomes](results/current/primary-outcomes.svg),
[resource outcomes](results/current/resource-outcomes.svg),
[trajectories](results/current/trajectories.svg) and
[presentation PDF](results/current/presentation.pdf).
[Reproduction instructions](reproduction.md),
[presentation walkthrough](presentation.md) and
[run/stop commands](run-project.md) are synchronized with `streamscale.sh`.
Bulky raw evidence and credentials remain ignored; compact measured results
and plots are committed. Review used local code inspection, regression tests,
live reconciliation and artifact inspection; CodeRabbit remote review was
unavailable because its optional CLI was signed out.

## Dashboard access follow-up, 2026-10-10

The user's complete M0–M8 run passed before UI startup failed because loopback
port 13000 was occupied. Interactive access now prefers 13000/19090 and falls
back to available ports when defaults are busy, printing the actual URLs.
`streamscale.sh ui` and `all` accept explicit `--grafana-port` and
`--prometheus-port` options, including 0 for automatic allocation. An occupied
explicit port fails with a short actionable error. `make k8s-ui` uses the
maintained launcher. README, launcher help and run instructions are synchronized.

Interactive startup lives in `deploy/ui.py` and uses the existing forward
context manager. The measured runner and its forwarding primitive were left
unchanged, so the preserved experiment fingerprint remains valid. No archived
measurement or report artifact changed during regeneration.

`./streamscale.sh all --no-ui` exited 0, observed at 2026-10-10T04:00:07Z:
161 unit tests, 28 live integration tests, clean Ruff, fresh M5 reconciliation,
M6 peaks 1/3/3 with 5,940 facts in each mode, full retained M7 audit and M8
regeneration. The console log is `artifacts/ui-fix-full-check.log`.

Live conflict checks held both default ports open while verifying healthy
Grafana/Prometheus responses on alternate ports. SIGINT and SIGTERM each closed
only the new command's forwards; the occupied listeners remained accessible.
An explicitly requested occupied port returned status 1 without a traceback.
Evidence is in `artifacts/ui-port-conflict-check.json`. Services, volumes, nodes
and earlier run data were retained. Local review and regression checks passed;
CodeRabbit remained signed out.
