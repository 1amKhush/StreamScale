# M11 evidence: interactive sale controls

Started 2026-10-11T22:20Z, after M10 (`8988892`). Acceptance comes from
`make console-check`; the log is `artifacts/console/m11-console-check.log`.

## Scope delivered

- **Sale panel**: measured presets plus saved presets (`artifacts/console/presets.json`),
  seed, up to twelve schedule phases, malformed/duplicate/late percentages,
  client-side validation matching the agent's limits, and an estimate of events,
  orders, DLQ items, duplicates, late events and the queue one, two or three
  consumers would build at the M7 pilot capacity. *Run the last sale again*
  repeats settings as a fresh run.
- **Live controls**: hold/reopen the doors (pause/resume the schedule clock),
  a live rate or a return to the schedule from the current position, end and
  drain, and abort with confirmation (unsent records purged, sent records
  finished). Publisher progress shows sent, acknowledged, awaiting
  acknowledgement, delivery failures and buffering.
- **Partial-run handling**: every sale is finalized as `published`; the console
  reconciles each drained run against its Kafka source range, SQL and the DLQ
  and reports exact SQL p50/p95/p99. Pacing changes mark a session as not
  comparable.
- **Restart safety**: a restarted server reattaches to a running or draining sale,
  reconciles drained sessions that missed reconciliation, and marks sessions
  from runs that are no longer current as orphaned; an agent restart finalizes
  the run as `interrupted` from its acknowledgement log.
- **Duplicate protection**: one idempotency key per intent in the browser, server
  checks against its own session record, and controls that appear under the
  pointer are disarmed for 0.7 s after a start.

## Defects found and fixed during acceptance

1. A reconciliation queued while the agent forward was not yet up failed instead of
   waiting; it now waits up to two minutes for the agent.
2. The live rate test exhausted the steady preset's 3,182-event budget at 400/s,
   so the sale legitimately finished before the test asked it to follow the
   schedule again. Long tests now use a ten-minute custom schedule.
3. Browser tests counted sale starts in the server's 200-entry journal window,
   which rolls over in a long check, and could leave a sale running after a
   failure. They now count by timestamp and stop any active sale afterwards.
   The sale panel also disables its start button while a previous sale drains.

## Acceptance

Full run: `make console-check` exit 0, 2026-10-11T22:32:35Z–22:37:28Z (console
image `streamscale-console:481a40dc2fbd7270`): 98 Python unit tests, 19 web unit
tests, 23 live-cluster tests in 211 s and 5 Chrome tests. After review, the M11
live tests were tightened to wait for the *new* sale's acknowledgements (an
earlier sale's last sample had satisfied the abort test's wait, so it aborted
after 20 purged sends); the five M11 live tests then passed again in 112 s,
2026-10-11T22:38–22:40Z. Every figure below is from that rerun.

| Scenario | Outcome | Acknowledged | Facts | Missing | Duplicates stored | State regressions | Exact SQL p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hold the doors, rate 400 → schedule, then stop | stopped | 3,589 | 3,478 | 0 | 0 | 0 | 11.57 s |
| Abort during the flash-sale rush | aborted | 603 | 584 | 0 | 0 | 0 | 1.09 s |
| Agent pod deleted mid-sale | interrupted | 2,143 | 2,082 | 0 | 0 | 0 | 7.90 s |
| Console server restarted mid-sale | stopped | 326 | 316 | 0 | 0 | 0 | 1.00 s |

Facts are fewer than acknowledgements because invalid records go to the DLQ and
deliberate duplicates are stored once; reconciliation checks both explicitly.

Acceptance criteria:

- **Pause stops new production while callbacks and consumers progress.** Over six
  seconds of hold, sent count, acknowledged count and the topic's high watermarks
  were unchanged, while stored facts increased and lag fell. In Chrome, the
  acknowledged count stayed constant for four seconds of hold.
- **Rate changes work.** At a live 400/s the measured arrival rate exceeded 300/s;
  *Follow the schedule* returned it below 120/s (the schedule's 52/s). In Chrome,
  a 300/s rate raised the measured arrival rate above 220/s.
- **Stopping accounts for acknowledged events.** A stopped sale ended with sent
  equal to acknowledged and zero delivery failures; aborted and interrupted
  sales reconciled on what reached the log, with the interrupted run's seven
  in-flight records counted as in the log without an acknowledgement.
- **Reconnect/restart restores actual state.** Restarting the console server
  mid-sale reattached the session (the console held the cluster lock again and
  journalled the reattachment); deleting the agent pod mid-sale finalized the
  run as `interrupted`, and it drained and reconciled.
- **Double-click does not publish twice.** Repeating an idempotency key returned
  the original operation; a second start with another key was refused while a
  sale ran; a double click on the panel's start button started exactly one sale.

Screenshots: `artifacts/console/e2e/sales-paused.png`, `sales-result.png`.
