# M14 evidence: replay, comparison, exports and guided demonstrations

Started 2026-10-12T02:32Z, after M13 (`57c427d`). Live acceptance:
`make console-check` live stage, 40 tests passed in 1,291 s
(`artifacts/console/m14-console-check.log`, console image built from this tree).
That run's browser stage exposed an intermittent hidden-node defect (below);
after the fix the complete Chrome suite passed twice in a row, 8/8 each
(`artifacts/console/m14-browser-round1.log`, `m14-browser-round2.log`).
Unit tests: 138 Python (adds session, export and matrix API tests), 19 web.

## Scope delivered

- **Sessions view** (top bar *Live / Sessions*): every sale's configuration,
  journal, reconciliation and one-second telemetry, now including a compact
  per-second sample (offsets, group membership, pods, controllers, SQL counts,
  DLQ codes, faults) so replay redraws the whole floor. Older sessions without
  samples replay their exact rates, lag and replica counts and say what is missing.
- **Replay**: scrubber, play/pause, 1×/4×/10×, labelled *not live*, with no pipeline
  controls; it reads recorded files only.
- **Exports**: `points.csv`, `configuration.json`, `journal.jsonl`,
  `reconciliation.json` per session, from an allowlist.
- **Formal comparison**: *Run fixed, CPU-HPA and lag-KEDA* runs one frozen preset
  per mode sequentially as fresh runs and groups, waits for each drain and
  reconciliation, and ranks only eligible sessions. It runs as a background
  orchestrator that submits ordinary operations, so stop and pause stay available.
  Eligibility excludes aborted, stopped or interrupted sales, sessions with
  faults, scaling or pacing changes, and missing or failed reconciliation, and
  states the reason. Sessions are also grouped by identical logical workload.
- **Measured matrix**: the audited 27-run tables and figures, read-only.
- **Guided demonstrations**: *No scaling*, *Manual rescue*, *CPU versus lag*,
  *Database bottleneck* and *Crash and replay*, step by step with action buttons and
  what to watch, plus the Store/System glossary with the analogy's limits.
- **Presentation**: full-screen mode (`p`, `Esc`), lens (`l`) and motion (`m`)
  shortcuts that ignore typing.

## Defect found and fixed

Intermittently, the lane and floor-manager nodes stayed in the DOM but hidden
during a sale. React Flow hides a node until it has measured it; the floor rebuilt
its node list every second without the measured sizes, so a node whose size never
changed again could stay unmeasured. The floor now keeps nodes in state, applies
React Flow's dimension changes, and merges each second's data into the measured
nodes. Two consecutive full browser runs passed afterwards.

## Acceptance

Formal comparison `comparison-20261012024007-6da1fc` (flash-sale preset, seed 202),
2026-10-12T02:40:07Z–02:45:28Z, all three runs eligible:

| Mode | Exact SQL p95 | Peak lag | Most members | Replica-seconds | Facts |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fixed | 32.3 s | 4,882 | 1 | 87 | 9,781 |
| CPU-HPA | 0.97 s | 157 | 3 | 231 | 9,781 |
| Lag-KEDA | 0.95 s | 151 | 3 | 195 | 9,781 |

Both autoscalers reached three replicas during the preset's 15-second quiet phase
(one consumer at 52/s already exceeds the 60% CPU target, and lag briefly exceeded
the KEDA threshold), so the rush met three consumers. The M7 protocol instead ran
a separate warm-up before each measured cell. This single sequential comparison is
descriptive; the audited 27-run matrix remains the measured result.

Acceptance criteria:

- **Playback matches recorded evidence.** The live test compared every replay frame
  served by the console with the session's `telemetry.jsonl` line by line, checked
  that each frame's per-partition lag equals the lag in its stored sample, and that
  `points.csv` has one row per frame. In Chrome, scrubbing to a recorded index
  showed exactly that frame's lane lags, and playback advanced the replay clock.
- **Comparisons exclude unsuitable runs.** The three comparison runs were eligible
  and shared one workload signature; interactive sessions from earlier tests were
  listed as excluded with reasons such as *faults injected*, *scaling changed during
  the sale* and *pause or rate changed*.
- **Existing 27-run artifacts remain unchanged.** SHA-256 of every CSV/JSON in
  `docs/results/current/` was identical before and after the comparison; the
  console serves them read-only; the M7 campaign fingerprint still matches.

Screenshots: `artifacts/console/e2e/history-replay.png`, `history-matrix.png`.
