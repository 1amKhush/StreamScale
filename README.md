# StreamScale

A Python Kafka-to-PostgreSQL flash-sale analytics experiment comparing fixed,
CPU-HPA and lag-KEDA consumer scaling. **M0–M8 are implemented and validated:** the contract,
local pipeline, seeded publisher, safe fault/replay handling, and a working
kind/Helm deployment with Prometheus and Grafana. M6 adds mutually exclusive
fixed, CPU-HPA and lag-KEDA controllers. The M7 runner adds a pilot-calibrated,
frozen 27-run matrix with SQL latency measurements and raw evidence.
All 27 measured cells passed reconciliation and controlled restart; see
[M7 evidence](docs/m7-evidence.md).
M8 produces standalone scientific plots, a report and a presentation PDF after
the complete matrix passes its independent evidence audit.
The complete launcher passed with 161 unit tests, 28 live integration tests and
all three scaling modes. See [the measured report](docs/results/current/report.md),
[presentation PDF](docs/results/current/presentation.pdf) and [M8 evidence](docs/m8-evidence.md).

**M9–M15 add an interactive console**: a local browser console for the real kind
deployment that explains the pipeline as **a supermarket checkout during a flash
sale** (lanes are partitions, cashiers are consumer pods, the floor manager is the
scaling controller, the ledger is PostgreSQL), with Kafka/Kubernetes/SQL names
always available. Plan and control real sales, watch backlog build on a live floor
map and synchronized charts, switch between fixed, manual, CPU-HPA and lag-KEDA
or take over manually, inject bounded failures and watch recovery, then replay,
reconcile and compare sessions. Start it with `./streamscale.sh console`; see
[the console guide](docs/console.md), [its plan](docs/interactive-console-plan.md)
and the [M9](docs/m9-evidence.md)–[M15](docs/m15-evidence.md) evidence. The complete
M0–M15 launcher, including the console's live-cluster and browser acceptance,
passed on 2026-10-12.

One command runs every integrated stage and opens the console and dashboards:

```sh
./streamscale.sh all
```

It prepares the environment, checks the local pipeline and fault cases, deploys
and verifies a fresh cluster run, runs the console's live and browser acceptance,
then serves the console with dashboard links. Add `--no-ui` to finish after verification. See [the complete run guide](docs/run-project.md)
for commands from a fresh checkout, expected results and shutdown/resume.
Dashboard access chooses another port if a default is busy; open the printed URLs.
Use `./streamscale.sh ui --grafana-port 13001 --prometheus-port 19091` to choose ports.
`./streamscale.sh stop` stops both project stacks while retaining data;
`./streamscale.sh resume` starts their retained services again.
Cluster verification waits for refreshed Prometheus samples before checking
the completed run; `./streamscale.sh verify` repeats that check without republishing.
`./streamscale.sh scaling` exercises all three modes and their 1–3 bounds; see
[scaling operations](docs/scaling.md). It leaves a fresh fixed demo running afterward.
`./streamscale.sh experiments` runs/resumes the measured campaign and verifies
preserved checksums when reusing completed results. Use `--fresh` for a new
campaign. See [experiment protocol and evidence](docs/experiments.md).
`./streamscale.sh analysis` regenerates the report from completed raw measurements.
Results are written to `docs/results/current/`; see [reproduction](docs/reproduction.md)
and [the presentation walkthrough](docs/presentation.md).

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker Engine with
Docker Compose, and `make`. The console also needs Node.js 20.19+ with npm, and its
browser checks use the installed Google Chrome. Run these commands from the repository root:

```sh
test -f .env || cp .env.example .env
uv sync --frozen
make up
make bootstrap
make smoke
make demo RUN_DIR=artifacts/my-first-run
make test
make integration
make check
```

`make up` waits for healthy Kafka and PostgreSQL. `make bootstrap` explicitly
creates/verifies the two topics and applies checksum-protected SQL migrations;
it is safe to repeat with retained volumes. `make smoke` publishes fresh valid
events with `order_id` keys, waits for acknowledgements, consumes this run's
records from all three partitions, and checks a committed PostgreSQL decimal
round trip. It writes no business facts and commits no source offsets.

`make demo` generates a seven-attempt fixture workload, registers/publishes it,
consumes its captured source range and inspects SQL/DLQ results. Expected: **four
facts, two latest states, one duplicate attempt, two invalid/DLQ sources, zero
missing valid IDs and zero state regressions**. Its `--no-wait` skips publish
pacing for a quick correctness demonstration. Use a new `RUN_DIR` each time;
generating over an existing directory or republishing an execution is refused.
SQL rows and broker records remain available for inspection.

Kafka is available at `127.0.0.1:19092`; PostgreSQL at `127.0.0.1:15432`.
Local credentials are in the ignored `.env`. Change matching connection settings
there when overriding ports or credentials. Both ports bind to loopback only.
`make down` stops the stack and preserves its volumes.

To run the cluster demo, keep Docker running and execute:

```sh
make k8s-up
make k8s-verify
make k8s-ui
```

`make k8s-up` downloads checksum-verified kind/kubectl/Helm tools if needed,
builds and loads the pinned Python image, and installs/upgrades a dedicated
`streamscale` cluster/release. Each invocation creates a fresh run/group and
keeps previous SQL rows, broker records and run artifacts. The default 200-order
correctness workload produces **408 acknowledgements, 396 unique facts, 200
states, eight duplicates and four invalid/DLQ sources**.

`make k8s-verify` checks SQL/DLQ results, one ready consumer, metric values and
run labels, scrape health and Grafana provisioning. It copies raw evidence to
`artifacts/k8s/<run-name>/`. `make k8s-ui` keeps loopback port forwards running:
Grafana prefers **http://127.0.0.1:13000/d/streamscale**, Prometheus prefers
**http://127.0.0.1:19090**; busy defaults fall back to the URLs printed by the command.
Grafana's generated login is in
`artifacts/k8s/credentials.json`; keep the command running while viewing it.
Use `make k8s-status` or `make k8s-logs` to inspect the service.

| Location | Responsibility |
| --- | --- |
| [Contract decisions](docs/contract-v1.md) | Event/run identity, late-event rule, faults, measurement definitions |
| `schemas/` | Versioned order event and DLQ JSON Schemas |
| `src/streamscale/` | Contract helpers, local bootstrap, migrations and smoke CLI |
| `src/streamscale/storage.py`, `consumer.py`, `dlq.py` | Durable record handling and safe at-least-once replay |
| `tools/workload/` | Offline seeded generator and acknowledged publisher, separate from the consumer loop |
| `db/migrations/` | Three business tables, constraints and immutable history |
| `deploy/local/` | Digest-pinned single-broker KRaft Kafka and PostgreSQL |
| `deploy/k8s/` | Pinned kind tools, Helm chart, image build, cluster verification and UI access |
| `src/streamscale/metrics.py`, `service.py` | Run-labelled counters/histograms, health endpoints and singleton Kafka exporter |
| `tests/` | Contract fixtures, bootstrap guards and live integration tests |
| `experiments/` | Pilot, frozen matrix, SQL measurements, evidence audit and separate plotting environment |
| `docs/results/current/` | Audited 27-run CSV snapshot, plots, interpretation and presentation PDF |
| [Local operations](docs/local-development.md) | Manual publish/consume, inspection and troubleshooting |
| [Milestone evidence](docs/m0-m1-evidence.md) | Actual commands, results, review and remaining scope |
| [M2/M3 operations](docs/workload-and-consumer.md) | Generate/publish/consume/inspect commands and crash replay |
| [M2/M3 evidence](docs/m2-m3-evidence.md) | Workload reconciliation, SQL atomicity and real process-crash checks |
| [M4 fault handling](docs/fault-handling.md) | Outage/backoff, DLQ failures, crash injection and rebalance behavior |
| [M5 deployment operations](docs/deployment-and-metrics.md) | Cluster commands, metric definitions, artifacts and troubleshooting |
| `console/` | Interactive console: loopback API, in-cluster agent, console consumers, React UI |
| [Console guide](docs/console.md) | Starting the console, the checkout analogy, safety scope and checks |

The event contract uses exact decimal **strings**, fresh run IDs and globally
distinct run-scoped event IDs. PostgreSQL is committed before source offsets in
the consumer; invalid records require acknowledged DLQ delivery. Processing
is at-least-once. No throughput improvement or exactly-once result is claimed.
