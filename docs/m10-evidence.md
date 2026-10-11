# M10 evidence: live flow, charts and inspection

Started 2026-10-11T21:08Z, after M9 (`bc8fdf8`). Acceptance figures below come
from `make console-check` on branch `interactive-console`; the log is
`artifacts/console/m10-console-check.log` and the summary `artifacts/console/check.json`.

## Scope delivered

- **Checkout floor** (React Flow): store doors, three lanes, dynamically rendered
  cashier pods, floor manager, sales ledger and problem-items desk. Lanes show
  queue length, five-second trend, high watermark, committed offset and owner;
  cashiers show partition assignment from the Kafka group (matched to pods by
  IP), status in words (serving, standby, waiting, retrying, collapsed with exit
  code, OOMKilled, leaving), CPU against request and limit, memory and restarts.
- **Sampled animation**: at most six particles per edge, scaled with
  log10(rate); *Motion off* and `prefers-reduced-motion` remove them while every
  value stays. Edges carry the measured per-lane rates as labels.
- **Signals** with evidence: backlog growing, no consumer owning partitions,
  consumers saturated, database slow, broker or database unavailable, consumer
  retrying, and a qualified drain-time estimate.
- **Synchronized charts** (uPlot, one clock, linked crosshair, one measure per
  chart, gaps for unknown values, action markers, table view): arrivals vs
  served, queue by lane, live p95 wait, cashier counts, CPU vs HPA target, DB
  p95, problem items, collapses and recoveries. Colours come from a validated
  palette (lanes slots 1–3; other series a second validated set).
- **Inspector**: lanes, cashiers (per-process counters and last termination),
  controller conditions, the latest stored facts, the latest DLQ envelopes, and
  order/event lookup showing broker acknowledgement, ledger history, latest
  state and DLQ reason.
- **Sale engine** in the agent (used here with start and end): seeded manifest
  from the unchanged generator plus a seeded fault mix, unchanged wire format
  and validation, acknowledgement-only log, schedule pacing; every sale is
  finalized as `published` so the unchanged consumer drains it. Presets reuse
  the frozen M7 rates (52/s, 390/s) and seeds. Sessions record configuration,
  journal and one-second telemetry under `artifacts/console/sessions/`.
- **Log-based reconciliation** in the agent (`console-reconciliation.json`),
  triggered after each drain, with exact SQL latency percentiles.

## Defects found and fixed during acceptance

1. After a server restart, backfilled history replayed old sale states into the
   session logic and reattached a long-finished sale, which then never drained.
   Backfilled samples now feed charts only; lifecycle decisions use live samples,
   and a session superseded by a newer run is closed.
2. A second `sale.start` (and `sale.stop` right after a start) was judged against
   a telemetry sample taken before the first sale began. Checks now also use the
   server's own session record, which changes immediately.
3. The agent-loss test could interrupt another test's sale. It now waits for an
   idle floor. (The interrupted sales it produced were finalized as
   `interrupted` by the agent's recovery path, as designed.)

## Acceptance

Final run: `make console-check` exit 0, 2026-10-11T22:16:17Z–22:19:00Z, console
image `streamscale-console:481a40dc2fbd7270` on `streamscale:dba6e7e963183912`.

| Stage | Result |
| --- | --- |
| Console Python unit tests | 98 passed (scope, derivation, operations, API security, sale engine, session lifecycle) |
| Web type check / unit tests / build | passed / 14 passed / built |
| Live cluster acceptance | 18 passed in 111 s |
| Chrome browser acceptance | 4 passed in 43 s |

Acceptance criteria:

- **A real sale updates topology, backlog and outcomes.** The live test started the
  measured flash-sale preset with one consumer. During the 390/s rush every
  partition exceeded 200 lag, arrivals exceeded the drain rate, and the single
  group member owned partitions 0, 1 and 2. After the drain, the unchanged
  `streamscale inspect` reconciler passed for the console run: 10,078
  acknowledgements, 9,781 unique facts, zero missing valid IDs, zero duplicate
  stored IDs, zero state regressions and every invalid record in the DLQ. The
  console's own log-based reconciliation agreed (99 invalid sources, exact SQL
  p95 32.2 s and 33.0 s in two runs with one consumer).
- **SQL and DLQ outcomes are inspectable.** The live test fetched the latest facts,
  looked up an order (history and latest state) and an event (acknowledgement
  partition/offset equals the stored fact's), and read DLQ envelopes with error
  codes. The browser test opened the ledger and problem-desk inspectors during
  the sale.
- **Usable under burst load and with reduced motion.** During the browser-driven
  burst, particles stayed at or below six per rendered edge; turning motion off
  removed all particles while the ledger count kept changing. A double click on
  *Open the doors* started exactly one sale. Screenshots:
  `artifacts/console/e2e/flow-burst.png`, `flow-inspector-desk.png`, `flow-drained.png`.

Stopped and interrupted sessions reconciled on their acknowledged subsets (for
example 2,610 acknowledged, 2,530 facts, 27 DLQ sources, zero missing). The M7
campaign fingerprint is unchanged. Two observations carried into M11: buttons
that appear where *Open the doors* was can receive the second click of a double
click, and a reconciliation queued before the agent forward is ready failed
instead of waiting.
