# Run and stop StreamScale through M5

Start Docker Engine/Desktop first. You need Bash, uv and make installed.
The launcher checks prerequisites and creates the ignored `.env` from
`.env.example` only when it is missing. Existing settings are preserved.

From the repository root, run everything with:

```sh
./streamscale.sh all
```

This performs setup, local smoke and fixture demonstration, unit/lint/integration
checks (including M4 faults), cluster image build/deployment, SQL/metrics/Grafana
verification, and finally dashboard access. The last step stays in the terminal
until Ctrl+C. First-time image/tool downloads can take several minutes. For a
complete check without starting dashboard forwards:

```sh
./streamscale.sh all --no-ui
./streamscale.sh ui
```

Every local and cluster demo gets a fresh directory, run ID and consumer group;
previous data is kept. Local launcher artifacts live under
`artifacts/launcher/demo-*/local-run/`. The latest local demo can be reinspected
with `./streamscale.sh inspect-local`. Cluster evidence lives under
`artifacts/k8s/<run-name>/`.

Expected local fixture result: seven acknowledgements, four unique facts, two
latest states, one duplicate attempt and two invalid/DLQ sources. Expected default
cluster result: 408 acknowledgements, 396 unique facts, 200 states, eight duplicates
and four invalid/DLQ sources. Both should have zero missing valid IDs, zero
duplicate stored IDs, zero state regression and zero lag after drain.

Cluster verification allows up to 60 seconds for the independent Prometheus
scrapes to match the completed run. It requires zero lag in all three partitions
and a Kafka exporter sample newer than SQL reconciliation. Persistent backlog,
incorrect counters or missing series still fail with their observed values.
To repeat verification without rebuilding or republishing, run
`./streamscale.sh verify`.

Open the dashboard while the UI command runs:

- Grafana: http://127.0.0.1:13000/d/streamscale
- Prometheus: http://127.0.0.1:19090

View `artifacts/k8s/credentials.json` for Grafana's generated username/password.
In Grafana, choose the current run UUID from the Run ID dropdown. The verified
UUID and current cluster directory are printed by the launcher; metadata also
lives in `artifacts/k8s/deployment.json`. This is an analytics pipeline with
Grafana monitoring and SQL/artifact inspection; M6 scaling and M7–M8 measured
experiments/analysis are not implemented yet.

To run the individual stages manually instead of the launcher:

```sh
# M0/M1: environment, executable contract checks, local foundation
test -f .env || cp .env.example .env
uv sync --frozen
make test
make check
make up
make bootstrap
make smoke

# M2/M3: fresh generated fixture -> Kafka -> SQL/DLQ -> reconciliation
# Change RUN_DIR each time, since existing directories are never overwritten.
make demo RUN_DIR="artifacts/manual-$(date -u +%Y%m%dT%H%M%S)-$$"

# M4: outage, crash, replay and rebalance campaign
make faults
# Full live suite, including paced publisher/consumer behavior:
make integration

# M5: pinned tools/image, kind/Helm, fresh cluster run, metrics and dashboard
make k8s-up
make k8s-status
make k8s-verify
make k8s-ui
```

`make k8s-up` installs kind/kubectl/Helm automatically when missing. Standalone
`./streamscale.sh cluster` also starts retained kind nodes before deployment,
which is useful after stopping them.

To stop after viewing the project:

```sh
# In the dashboard terminal, press Ctrl+C to close its two port forwards.
# In a terminal at the repository root:
./streamscale.sh stop
```

`stop` stops containers matching the project's Compose label and its kind cluster
label. Stopping the kind node containers stops all pods and the Kubernetes
control plane inside them. Containers, volumes, SQL rows, broker records, and
run artifacts remain available for a later resume. This command does not need
uv, make, credentials, network downloads or a working Kubernetes API.

For separate shutdown or restart:

```sh
./streamscale.sh stop-local       # Local Kafka and PostgreSQL
./streamscale.sh stop-cluster     # The kind cluster and every pod inside it
./streamscale.sh resume           # Start retained stacks; wait for API and services
./streamscale.sh ui               # Reopen dashboard access
./streamscale.sh status           # List project containers and Kubernetes resources
```

`resume` keeps the existing execution. Consumer counters/histograms reset when
their process restarts, so use `./streamscale.sh cluster` or `all` to publish a
fresh execution before running the strict initial-run metrics verification.

If you prefer the underlying shutdown commands:

```sh
make down                       # Remove local containers/network; named volumes stay
docker stop --timeout 45 streamscale-control-plane
# For this chart's one-node cluster; resume via the launcher.
```

Deleting the kind cluster removes the data inside its node containers. Normal
shutdown uses `stop`, which retains those containers. No global Docker prune,
volume deletion, or cluster deletion is part of this launcher.

For later milestones, `AGENTS.md` requires the integration to update
`streamscale.sh`'s full workflow/help, supporting Make targets, this guide and
README together. The launcher is the maintained entry point for the current
integrated project, rather than a separate fixed demo.

Verified on 2026-10-08: `./streamscale.sh all --no-ui` completed successfully,
with 102 unit tests, 28 live integration tests, clean Ruff checks, and a fresh
cluster run (`run-20261008093839-fb24707c`) passing SQL, Prometheus and Grafana
verification. The cluster stored 396 unique facts and 200 states with zero
missing valid IDs and zero lag. The full console log is
`artifacts/launcher-full-check.log`, and cluster evidence is in that run's
directory. Shutdown/resume scoping was tested with mocked containers; running
user services were not stopped for verification.

After correcting the Prometheus scrape timing race, the full launcher passed
again with 110 unit tests and 28 live integration tests. Fresh cluster evidence
is in `artifacts/k8s/run-20261008095358-3edb7b43/`; the console log is
`artifacts/launcher-metrics-fix-check.log`. The user's earlier failed execution
also passed the corrected verifier without republishing.
