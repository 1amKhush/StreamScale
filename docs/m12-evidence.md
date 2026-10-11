# M12 evidence: scaling modes and manual override

Started 2026-10-11T22:41Z (integration of the scaling controller drafted during
M10/M11), after M11 (`7898931`). Acceptance: `make console-check`, exit 0,
2026-10-11T22:41:37Z–22:53:30Z, console image `streamscale-console:a17efb925dc53122`
on `streamscale:dba6e7e963183912`. Log: `artifacts/console/m12-console-check.log`.

## Scope delivered

- **Modes** for console consumers: fixed (1 replica), manual (0–3), CPU-HPA (1–3,
  CPU target of the request) and lag-KEDA (1–3, lag threshold per replica). HPA
  and ScaledObject specs match the measured M6/M7 modes (same scale-up/down
  behaviour, 60% CPU target, lag threshold 100, `allowIdleConsumers: false`).
- **Manual override** from an autoscaled mode removes the autoscaler before writing
  replicas; releasing it recreates the selected controller.
- **Single-controller ownership**: every transition removes unwanted controllers,
  waits until they and KEDA's generated HPA are gone, writes replicas, creates the
  wanted controller and verifies ownership. Desired replicas, ready pods and
  consumer-group members are shown separately. Transitions run in the agent,
  are persisted, and an agent restart reports an interrupted transition with a
  *Reapply* action instead of claiming success.
- **KEDA follows each sale**: a new sale's consumer group is written to the
  ScaledObject before its consumers join.
- **Settings**: CPU target, lag threshold, scale-down wait, minimum and maximum
  (never above three partitions), validated in the agent.
- **Staffing tab** with the store/system wording, live ownership, transition
  progress, controller conditions and recent transitions. Mode, override and
  replica changes during a sale are recorded on its session, which keeps it out
  of formal comparisons.
- The agent's Role already named exactly these resources (M9); the console's HPA
  and ScaledObject never target the measured `streamscale-consumer`.

## Acceptance

| Stage | Result |
| --- | --- |
| Console Python unit tests | 114 passed (adds 12 scaling transition tests) |
| Web type check / unit tests / build | passed / 19 passed / built |
| Live cluster acceptance | 28 passed in 568 s |
| Chrome browser acceptance | 6 passed in 2.3 min |

Live sessions from this run (all reconciled with zero missing acknowledged IDs,
zero duplicate stored IDs and zero state regressions):

| Scenario | Modes in the session | Peak lag | Max desired · ready · members | Mean drain at 1 → 3 members | Exact SQL p95 |
| --- | --- | ---: | --- | --- | ---: |
| Fixed flash sale | fixed | 5,003 | 1 · 1 · 1 | 124/s → — | 33.1 s |
| Manual rescue, extended sale | fixed → manual 3 | 4,207 | 3 · 3 · 3 | 92/s → 384/s | 10.5 s |
| CPU-HPA flash sale | cpu-hpa | 3,908 | 3 · 3 · 3 | 95/s → 183/s | 9.9 s |
| Lag-KEDA flash sale | lag-keda | 3,838 | 3 · 3 · 3 | 99/s → 176/s | 9.6 s |
| Override at 1, then release | lag-keda (override) | 6,225 | 3 · 3 · 3 | 103/s → 346/s | 17.9 s |
| Zero replicas, then one | manual | 185 | 1 · 1 · 1 | — | 4.3 s |

Mean drain values average every sample with that member count, including ramp-up
and tail, so they understate steady throughput. These are single interactive
runs on one local node: illustrations, not a ranking (formal comparisons are M14;
the audited 27-run matrix remains the measured result).

Acceptance criteria:

- **Under overload one consumer accumulates backlog.** Every fixed flash sale
  built a backlog of thousands of events with one group member owning all three
  partitions.
- **Manual three improves the drain when consumers are the bottleneck.** After
  switching to manual three, the group settled on one partition per member and the
  drain rate exceeded 1.8 times the single-consumer rate (asserted), roughly four
  times by session averages. The Chrome test drove the same rescue from the
  Staffing tab and saw three cashiers with lanes 0, 1 and 2 and no autoscaler.
- **Both autoscalers respond.** CPU-HPA and lag-KEDA each raised desired and ready
  replicas to three during the flash sale; the ScaledObject's consumer group
  equalled the new sale's group.
- **Transitions preserve offsets and enforce single ownership.** During each
  autoscaled sale exactly one HPA targeted the console Deployment; an override
  left no controller, held one replica for 15 seconds under backlog, and release
  restored KEDA, which scaled back up. Sessions with mode changes reconciled with
  no missing or duplicated facts. Zero manual replicas left no group members and
  raised the "no consumer owns a partition" signal without any pod restart.

Screenshots: `artifacts/console/e2e/scaling-before-rescue.png`, `scaling-after-rescue.png`.
