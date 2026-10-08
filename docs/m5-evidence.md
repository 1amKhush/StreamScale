# M5 acceptance evidence

Started 2026-10-08T00:26:31Z; final acceptance checks completed
2026-10-08T00:52:16Z. Docker Engine 29.8.1 had 12 CPUs / 16,474,660,864 bytes RAM.
Official binaries were checksum verified: kind 0.33.0, kubectl 1.37.0, Helm 4.3.0.
The dedicated one-node kind cluster runs Kubernetes 1.37.0. Kafka 4.1.2,
PostgreSQL 17.7, Prometheus 3.15.0, Grafana 13.2.3, Python 3.12.14 and the uv
build stage are version/digest pinned; Python dependencies use the frozen lock.

Commands exercised:

```sh
make k8s-up
make k8s-verify
make k8s-ui
make check
uv run --frozen pytest
uv run --frozen --env-file .env pytest -m integration
.tools/bin/helm lint deploy/k8s/chart
```

Final results: **95 unit tests**, **28 live integration tests** (68.90 seconds),
Ruff checks/formatting for 37 Python files, Helm lint, and the real cluster
acceptance check passed. A deliberate `helm template ... --set consumer.replicas=2`
was rejected by the schema, confirming M5's one-consumer boundary.

Final cluster execution:

| Evidence | Observed value |
| --- | --- |
| Run ID | `4bd822f4-7c32-457e-b970-2ea48c131cef` |
| Image | `streamscale:4d5fac71766c2a0c` |
| Git revision | `4555e652e824ec3a0ab7c7de1af3e30bcb9fd228`, dirty working tree recorded |
| Source SHA256 | `483ad0026d9eee882f2c0ce64d8548eedf6ab3782ebfda96a77eb2305523b311` |
| Ready consumer replicas / Kafka members | `1 / 1` |
| Generated / broker-acknowledged attempts | `408 / 408` |
| Unique facts / state rows | `396 / 200` |
| Duplicate handling / invalid / DLQ copies | `8 / 4 / 4` |
| Unique invalid sources | `4` |
| Missing valid IDs / duplicate stored IDs / state regressions | `0 / 0 / 0` |
| Kafka lag after drain | Zero in all three partitions |
| Prometheus | Consumer and singleton exporter targets up; values and run/mode/scenario labels matched |
| Latency histograms | 396 unique-commit end-to-end observations; DB result series present |
| Grafana | Twelve-panel provisioned dashboard available via v1 API; Prometheus data-source health `OK` |

API verification completed at `2026-10-08T00:49:43.461599Z`. Raw evidence is in
`artifacts/k8s/run-20261008004458-19618015/`: manifest, acknowledgements, run metadata,
SQL/DLQ inspection, raw metrics, Prometheus samples/targets, consumer deployment,
Grafana dashboard and data-source health. The first run
`b4c8faf9-74c6-494f-8870-fb51b0e31d7d` separately passed with the same logical workload
and counts; its evidence remains in `artifacts/k8s/run-20261008003654-8a1a7bc0/`.
Upgrading used a new run/group and retained the earlier data.

The live paced-publication test accepted SQL facts before the publisher finished
its ten-second fixture schedule. The consumer then learned the captured end,
drained, and reconciled four facts, two states, duplicates and both DLQ sources.
This checks that service startup does not turn every paced workload into a
prepublished backlog. Generated totals are exported once per run, separately
from the consumer replicas; the exporter never subscribes/joins the group.

Both printed UI endpoints responded HTTP 200: Grafana `/api/health` on 13000 and
Prometheus `/-/ready` on 19090. Ctrl+C closed both forwards. The cluster and the
local Compose stack remain running for user inspection.

Defects fixed during implementation: the private credential writer used an
unsupported `Path.open` argument; the single-node Kafka controller was initially
unreachable behind a readiness-gated service. The writer now uses private file
creation, and the chart publishes the controller endpoint during startup. The
corrected setting is recorded in the deployed Helm release. No data reset was
performed. Metric semantics and lifecycle were reviewed locally; CodeRabbit was
signed out, so remote review was unavailable.

This is correctness/deployment evidence. No HPA, KEDA, measured 27-run matrix,
performance ranking or claimed throughput improvement is included. Next: M6.

## Verification scrape race corrected on 2026-10-08

The user's `run-20261008094521-b66d8378` reached 396 facts, 200 states and zero
source-offset lag, but verification failed with `Kafka backlog did not drain`.
The saved exporter metrics already showed zero in all three partitions.
Prometheus history at 09:46:15 UTC showed 396 accepted events while its older
partition-2 lag sample still reported 60; by 09:46:20 the reported lag was zero.
The original verifier stopped polling as soon as every series existed, before
the independent scrapes had converged. History and current-query evidence are
saved in that run's `backlog-race-evidence.json`.

The verifier now waits up to 60 seconds for expected counter values, zero lag
covering all three partitions, and an exporter success timestamp newer than SQL
reconciliation. Handling failures still fail immediately; sustained backlog,
wrong counters and absent series fail at the deadline with diagnostic values.
Eight regression tests cover stale backlog/counters, missing and partial series,
stale exporter samples, persistent backlog and actual handling failures.

The same execution passed the corrected verifier at
`2026-10-08T09:50:50.144903Z`, without republishing or deleting any data. All
110 unit tests and Ruff checks passed after the change.

The complete `./streamscale.sh all --no-ui` path then passed with a fresh
execution, `run-20261008095358-3edb7b43`: 110 unit tests, 28 live integration
tests, Ruff, local reconciliation and cluster SQL/metrics/Grafana acceptance.
The fresh cluster again stored 396 facts and 200 states with zero lag.
The console log is `artifacts/launcher-metrics-fix-check.log`.
