# StreamScale measured results

Campaign `campaign-20261111T133937-93aae9` completed 2026-11-11T14:58:11.855641Z. All **27 measured runs** passed acknowledgement/SQL/DLQ reconciliation before and after a controlled restart.

Current-campaign excluded attempts: **0**. The independent audit verified 3,813 raw samples, SQL metrics, CSV values, fresh consumer groups and unchanged actual consumer image/resource settings.

Protocol **v2**: pilot capacity 112.33 accepted events/second; frozen steady rate **44**, surge rate **336** events/second. Each scheduled measurement window is 60 seconds, after a separate 30-second cache warmup. Each execution uses a fresh run UUID and consumer group. Measured group startup is included.

Values below are **median [minimum, maximum] across three repetitions**. Latency is the median of three independently computed run p95 values, not a pooled percentile or an average of pod percentiles. No confidence interval or statistical significance is claimed.

| Scenario | Mode | Window throughput (facts/s) | Run p95 latency (s) | Maximum sampled lag |
| --- | --- | ---: | ---: | ---: |
| steady | Fixed | 42.68 [42.55, 42.77] | 0.75 [0.75, 0.76] | 35 [33, 36] |
| steady | CPU-HPA | 42.58 [42.57, 42.82] | 0.75 [0.74, 1.05] | 34 [32, 55] |
| steady | Lag-KEDA | 42.58 [42.55, 42.62] | 0.75 [0.75, 0.76] | 34 [33, 36] |
| flash-sale | Fixed | 95.23 [91.13, 118.52] | 36.19 [24.08, 39.57] | 4379 [3761, 4446] |
| flash-sale | CPU-HPA | 138.83 [131.80, 139.15] | 21.31 [20.20, 23.84] | 3768 [3747, 3771] |
| flash-sale | Lag-KEDA | 139.27 [139.10, 139.42] | 5.09 [4.53, 5.25] | 1441 [1314, 1501] |
| overload-recovery | Fixed | 110.30 [105.40, 110.45] | 68.07 [66.96, 69.75] | 7826 [7813, 7976] |
| overload-recovery | CPU-HPA | 166.15 [157.52, 211.13] | 40.29 [30.08, 41.67] | 7806 [6865, 7889] |
| overload-recovery | Lag-KEDA | 211.23 [211.20, 211.33] | 15.47 [5.71, 15.82] | 4050 [1869, 4120] |

![Primary outcomes](primary-outcomes.svg)

Dots are individual repetitions; diamonds are medians and whiskers span the observed range.

Steady median run p95 values were **Fixed 0.752s, CPU-HPA 0.746s, Lag-KEDA 0.754s**; the maximum observed ready-consumer count was 2. Their small differences are descriptive observations from three repetitions.

For **flash-sale**, Lag-KEDA had the lowest observed median run p95 (5.09 seconds). Its median ready replica-seconds were 245.90, compared with 89.01 for fixed. The paired table shows throughput and lag; these results describe this local campaign only.

For **overload-recovery**, Lag-KEDA had the lowest observed median run p95 (15.47 seconds). Its median ready replica-seconds were 267.90, compared with 118.03 for fixed. The paired table shows throughput and lag; these results describe this local campaign only.

| Scenario | Mode | Ready replica-seconds | Recovery after overload (s) | Observed recoveries |
| --- | --- | ---: | ---: | ---: |
| steady | Fixed | 71.01 [68.01, 71.03] | n/a | n/a |
| steady | CPU-HPA | 71.01 [71.01, 94.15] | n/a | n/a |
| steady | Lag-KEDA | 74.03 [71.05, 78.08] | n/a | n/a |
| flash-sale | Fixed | 89.01 [74.02, 90.01] | n/a | n/a |
| flash-sale | CPU-HPA | 103.02 [91.98, 104.01] | n/a | n/a |
| flash-sale | Lag-KEDA | 245.90 [240.96, 247.09] | n/a | n/a |
| overload-recovery | Fixed | 118.03 [118.00, 119.02] | 87.64 [86.93, 87.83] | 3 |
| overload-recovery | CPU-HPA | 155.93 [123.15, 194.86] | 39.71 [39.26, 42.75] | 3 |
| overload-recovery | Lag-KEDA | 267.90 [262.21, 295.01] | 39.72 [39.42, 39.85] | 3 |

![Resource outcomes](resource-outcomes.svg)

![Lag and ready replica trajectories](trajectories.svg)

Trajectory lines show each repetition separately, aligned to publication start; shading indicates the scheduled surge and the vertical line the end of the base window. Lines continue through confirmed drain. Ready pods differ from desired replicas and Kafka members.

The primary throughput counts unique facts ingested in the half-open 60-second window. It can be lower during a burst even when every event eventually arrives. `summary.csv` also reports processing-span and drain-inclusive throughput. Latency uses every unique accepted event, including the drained tail. Maximum lag is the maximum sampled sum of 12 broker-offset partitions. Recovery ends after five distinct fresh zero-lag observations, including their confirmation delay. Missing recovery or scaling response remains missing; it is never filled with zero.

The shared correctness profile delays 5% of the base event count (rounded down and selected from valid events) by ten seconds, alongside invalid/duplicate fixtures. These late attempts extend actual publication to about 70 seconds; `publish_seconds` records the observed duration. The primary window remains the frozen base 60 seconds for every mode. Delayed tail arrivals and the five-fresh-zero criterion constrain measured recovery, so similar recovery times can coexist with very different burst p95 latency. Input-rate labels describe the base logical schedule; fault attempts alter the observed attempt rate. The complete delayed tail is reconciled and included in p95.

Resource integrals cover publication through confirmed drain, before the controlled restart. They use actual observation times with left-step interpolation. Kubernetes CPU metrics are delayed averages, sampled every five seconds; missing leading measurements are disclosed by coverage columns. Maximum memory is a sampled sum across available consumer pods. These resource values are approximations, not billing measurements.

Image `streamscale:b946dfe0e9df5b21`; build digest `sha256:707b9f53b1c7265568b5663006376ef5f3d00e4d0ccbb083f496f3da3aab7c77`; Git `7e01206e1fb640c3adc5a9a23b67b95ebfaec8f6`; dirty build: `False`. The platform manifest IDs observed in pods are separately preserved in `audit.json`.

Consumer requests: 100m CPU / 128Mi; limits: 500m CPU / 256Mi per replica. Fixed uses one replica; CPU-HPA targets 60% of requested CPU; KEDA targets 100 Kafka records of lag per replica. Autoscaled modes are bounded at 1–12, with 12 input partitions. Controllers are alternative, never simultaneously attached to the consumer.

The same logical scenario manifest must match across all nine corresponding cells. Seed 4242 randomizes cell order. All rates/windows were frozen before measurement; no measured run was tuned individually. Run IDs, logical hashes, correctness outcomes and exact configuration appear in the companion CSV/protocol/provenance files.

The single kind node shares host CPU with Kafka, PostgreSQL, monitoring and the observer. The host, retained database growth, cache carryover, short windows, metrics/controller cadence and fresh-group startup may affect results. Randomized order reduces but does not eliminate carryover. Three repetitions give a small descriptive sample. Sampled peaks can miss intermediate maxima. This v2 60-second calibration is not directly comparable to the original 120-second fixed-rate proposal. The controlled restart occurs after measurement; crash-during-commit replay was separately exercised in M3/M4. The pipeline remains at-least-once, and DLQ redelivery can create extra copies. No production, multi-node or exactly-once improvement is claimed.

Reproduce with `./streamscale.sh all --no-ui`; reuse validates retained raw evidence. For new measurements use `./streamscale.sh experiments --fresh`, then `./streamscale.sh analysis`. Raw artifacts are ignored locally and identified by `artifacts/experiments/latest.path`. See [the protocol](../../experiments.md) and [presentation walkthrough](../../presentation.md).
