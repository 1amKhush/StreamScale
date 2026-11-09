# Twelve-partition capacity evidence

Status: partial acceptance on 2026-11-09; full predictive preparation and
end-to-end acceptance are still running. These measurements are separate from
the historical three-partition M7 report under `../current/`.

The configuration is twelve Kafka input and DLQ partitions, initial one consumer,
and a 1–12 range for autoscaling. Three replicas offered only two upward steps;
twelve provides room to observe scaling decisions, stabilization, manual rescue
and replica-seconds tradeoffs. Matching partition count gives extra consumers
useful work. It does not guarantee proportional throughput.

## Live correctness and scaling

The twelve-consumer check assigned one partition to each ready consumer, then
returned to one ready consumer owning all twelve partitions. Its sale reconciled
9,450 acknowledged attempts, 8,820 unique stored facts, 450 duplicate attempts
and 180 invalid sources with 180 DLQ outcomes. Missing acknowledged IDs, duplicate
stored IDs and state regressions were all zero.

Its exact SQL p95 was **7.286299 seconds**, above the two-second objective.
Correctness passed; the latency objective did not. This was a capacity acceptance
sale with deliberate late/invalid/duplicate events, not a held-out mode comparison.

CPU-HPA and lag-KEDA each reached five ready replicas in their independent
acceptance workloads, with configured bounds 1–12 and no competing controller.
These checks verify control behavior; they do not rank performance.

## Per-count calibration

One retained saturated trace per count, 2,000 events/s published for 20 seconds
followed by complete drain. Rates are median offset-commit deltas divided by
actual elapsed time, restricted to stable assignments, matching ready counts and
backlog on every partition. Consumer requests are 100m CPU / 128Mi, limits
500m CPU / 256Mi. These are empirical calibration estimates on the shared local
node/database, not confidence intervals or a replicated throughput benchmark.

| Consumers | Median committed events/s |
| ---: | ---: |
| 1 | 84.98 |
| 2 | 172.97 |
| 3 | 251.95 |
| 4 | 264.96 |
| 5 | 255.96 |
| 6 | 272.46 |
| 7 | 348.82 |
| 8 | 320.96 |
| 9 | 294.45 |
| 10 | 269.96 |
| 11 | 277.45 |
| 12 | 297.94 |

The measured curve flattens and varies beyond three replicas. Seven had the
highest median in this calibration; twelve was lower. The controller uses these
separate measurements and finite queue projections, including when every target
is infeasible. It does not multiply single-consumer throughput by replica count
or automatically prefer twelve during overload. Shared infrastructure contention
and uneven partition assignments remain relevant.

Machine-readable evidence: [acceptance.json](acceptance.json) and
[calibration.csv](calibration.csv). Full ignored traces remain under
`artifacts/predictive/`, with original run IDs, configurations and reconciliations.
Run `./streamscale.sh all --no-ui` to reproduce complete verification and
`./streamscale.sh predictive-evaluate` for the separate held-out comparison.
