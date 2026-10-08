# M5 deployment and metrics

Run from the repository root with Python 3.12, uv, make and Docker Engine.
The default workload is a quick correctness demonstration with pacing skipped;
it is not a measured scenario or evidence of a throughput improvement.

```sh
uv sync --frozen
make k8s-up
make k8s-status
make k8s-verify
make k8s-ui
```

The first command installs pinned dependencies. `k8s-up` installs version-pinned
kind 0.33.0, kubectl 1.37.0 and Helm 4.3.0 into ignored `.tools/bin/`, verifying
official release checksums. It builds the locked application, creates/reuses
the `streamscale` kind cluster, loads the image and installs/upgrades the Helm
release in namespace `streamscale`. Cluster configuration is stored in
`artifacts/kubeconfig`; commands explicitly select `kind-streamscale`.
Your default kubeconfig is not used.

Kafka and PostgreSQL in this cluster have separate persistent volumes from
the local Compose stack. Prometheus, Grafana and run artifacts also use volumes.
The local-path volumes are tied to this one-node kind cluster; they survive pod
restarts, not deletion of the cluster. First startup pulls the pinned dependency
images and can take several minutes. The validated host has 12 CPUs and 16 GB
of Docker RAM; this is environment evidence, not a minimum hardware benchmark.

The workload Job's init container waits for Kafka and applies the existing
migrations. The Job generates/registers a new run, then publishes once. A fresh
directory, UUID and consumer group are used on every `k8s-up`, including upgrades.
The run's seed/logical sequence remains reproducible. The consumer follows its
captured start offsets while publication is active, then learns the final end
offsets and drains only that execution. It stays in the group after drain for
monitoring. No workload generation runs inside the consumer loop.

The consumer uses UID 10001, a read-only root filesystem, a bounded batch and
explicit commits. Its requests are 100m CPU/128 MiB and limits 500m CPU/256 MiB.
The chart enforces exactly one fixed consumer for M5. `/health/live` checks the
HTTP process; `/health/ready` becomes ready after partition assignment and clears
on revocation/loss or shutdown. SIGTERM stops between records and closes the
client. Existing commit/replay rules apply during container restarts.

Prometheus scrapes each consumer pod separately through namespace-scoped pod
discovery. The singleton run exporter exposes workload totals and broker/group
lag without joining the group. Grafana provisions a Prometheus data source and
the twelve-panel `streamscale` dashboard. Configurations live in
`deploy/k8s/chart/files/` and are packaged with the chart.

| Metric | Definition |
| --- | --- |
| `streamscale_generated_total` | Number of generated publish attempts, including injected duplicates and invalids; authoritative run artifact counter. |
| `streamscale_published_total` | Acknowledged attempts, checkpointed during publication and finalized at completion; singleton artifact counter survives exporter restarts. |
| `streamscale_accepted_total` | New facts successfully committed by this consumer process. |
| `streamscale_duplicate_total` | Confirmed durable replay/duplicate identities handled by this process. |
| `streamscale_invalid_total` | Validation failures attempted, including replays. |
| `streamscale_dlq_total` | Acknowledged DLQ copies, including possible redelivery copies. |
| `streamscale_failed_total` | Record handling failures, with bounded `db`, `dlq`, `ownership` or `offset` stage. |
| `streamscale_late_total` | New facts whose projection update was suppressed by the ordering rule. |
| `streamscale_end_to_end_seconds` | Actual `produced_at` to successful unique DB commit; duplicates do not add latency observations. |
| `streamscale_db_seconds` | Full DB handling duration including retries/backoff, with accepted/duplicate/failed result. |
| `streamscale_kafka_lag` | Broker high offset minus group committed offset, one series per partition. An uninitialized group starts at the captured run floor. No SQL backlog estimate. |
| `streamscale_consumer_replicas` | Live members of the run's Kafka group, sampled by the singleton exporter. |
| `streamscale_exporter_last_success_unixtime` | Freshness of the last successful broker/group sample. On failure, readiness clears and the previous values remain timestamped. |

Application series use `run_id`, `mode` and `scenario`; partition/result/stage
have small fixed domains. Event and order IDs are never labels. Standard process
CPU and resident-memory metrics are scoped by scrape target/pod. Consumer
counters/histograms reset on process restart; they describe handling operations,
not persistent SQL inventory. Grafana's cumulative p95 panels describe the current
process. Prometheus retains past samples for seven days. M7 will reconcile/export
measurement windows before any performance ranking.

`make k8s-verify` is an initial-run acceptance check: before deliberate consumer
restarts, its counters should match the freshly published execution. It checks
SQL/DLQ reconciliation, zero lag, the image/source snapshot, one ready consumer,
run-labelled metric values, healthy scrape targets, the provisioned dashboard and
Grafana's connection to Prometheus. It copies the manifest, acknowledgement log,
run metadata, inspection, raw metric text and API responses to
`artifacts/k8s/<run-name>/`. Generated credentials are excluded from these exports.

Keep `make k8s-ui` running in a terminal and open:

- Grafana: http://127.0.0.1:13000/d/streamscale
- Prometheus: http://127.0.0.1:19090

Grafana login is saved with mode 0600 in `artifacts/k8s/credentials.json`. Select
the current UUID from the dashboard's Run ID dropdown. Ctrl+C closes only the
forwards started by this command. If those ports are occupied, choose others:

```sh
uv run --frozen python deploy/k8s/access.py --grafana-port 13001 --prometheus-port 19091
```

Useful inspection commands:

```sh
make k8s-status
make k8s-logs
.tools/bin/kubectl --kubeconfig artifacts/kubeconfig --context kind-streamscale -n streamscale get events --sort-by=.lastTimestamp
.tools/bin/kubectl --kubeconfig artifacts/kubeconfig --context kind-streamscale -n streamscale logs job/<workload-job-name> -c bootstrap
```

For a paced fixture run using the same chart/image, the management script also
accepts `--orders`/`--profile` and chart values include `run.paced`. Scaling
controllers, metrics-server/KEDA installation, the 27-run runner and CSV analysis
remain M6–M8. M5 adds no HPA or KEDA and does not run a measured benchmark.

Primary references: [kind release/node images](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0),
[Helm installation](https://helm.sh/docs/intro/install/),
[Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/),
[Grafana dashboard API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/dashboard/).
