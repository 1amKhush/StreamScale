# Run and stop StreamScale through M21

Start Docker Engine/Desktop first. You need Bash, uv, make, and Node.js 20.19+
with npm installed. The console's browser checks use the installed Google Chrome.
The launcher checks prerequisites and creates the ignored `.env` from
`.env.example` only when it is missing. Existing settings are preserved.

From the repository root, run everything with:

```sh
./streamscale.sh all
```

This performs setup, local smoke and fixture demonstration, unit/lint/integration
checks (including M4 faults), cluster image build/deployment, SQL/metrics/Grafana
verification, three scaling-mode exercises, the measured experiment matrix,
plots/report/presentation generation, the interactive console's unit, live-cluster
and browser acceptance, and finally the console with Grafana/Prometheus links.
The last step stays in the terminal until Ctrl+C. First-time image/tool downloads can take several minutes. For a
complete check without starting dashboard forwards:

```sh
./streamscale.sh all --no-ui
./streamscale.sh console     # or ./streamscale.sh ui for dashboards only
```

## Interactive console (M9–M15)

```sh
./streamscale.sh console            # deploy and serve; prints a login link
./streamscale.sh console --port 0   # any available port
./streamscale.sh console-check      # console unit, live-cluster and browser acceptance
```

Open the printed `http://127.0.0.1:18080/?token=…` link (another port is chosen
if 18080 is busy). The console also opens Grafana and Prometheus forwards and
links them; `--grafana-port`/`--prometheus-port` work as for `ui`. Ctrl+C stops
the console server and its forwards only. The in-cluster console agent keeps
running, so an active sale and its fault-recovery timers are never abandoned.
Only one console server runs at a time. The console needs a deployed pipeline
(`cluster` or `all`).

The overview shows four key metrics, the pipeline map and two charts. **More
metrics** reveals the other charts; **View options** contains the Store/System
lens and motion controls. **Connection & diagnostics** contains source health,
Grafana and Prometheus. The **List** flow view works with a keyboard and is the
default on phones. Clicking a component opens a drawer; Escape closes it.

For frontend-only edits while the console is already running, rebuild the assets
and refresh your browser (no cluster redeployment needed):

```sh
cd /home/khush/code_hub/projects/streamscale/console/web
npm run build
```

A first demonstration, all in the browser:

1. **Scaling** → **Fixed**. **Sale** → *Extended flash sale* →
   *Start sale*. Open **Overview** to watch arrivals reach about 390/s while one
   consumer serves about 140/s; every lane's queue grows and the floor flags the backlog.
2. **Scaling** → **Manual** → **3**. After a short handover each
   cashier owns one lane and the queues shrink.
3. **Failure lab** → choose the crash-after-DB-commit experiment → *Inject*.
   Watch exit 75, the restart, the replay and the verified single SQL fact.
4. **Sale** → *Stop & drain*. After the drain the console reconciles
   the run against Kafka, SQL and the DLQ. Expand **Reconciliation details** for
   exact SQL p95.
5. Repeat the sale under **CPU autoscaling** (CPU-HPA) and **Queue autoscaling**
   (lag-KEDA). After model preparation, try **ML Predictive** too. **Sessions →
   Comparisons** runs the four comparison strategies as fresh, frozen, sequential
   runs. **Docs → Guided demos** walks through five demonstrations.

`console-check` runs every console stage: Python and web unit tests, the web
build, deployment, live-cluster acceptance (sales, scaling, faults, replay,
comparisons and the launcher console lifecycle)
and Chrome acceptance including the full sale → overload → rescue → crash →
outage → recovery → reconciliation journey. It takes roughly 30–40 minutes and
writes `artifacts/console/check.json`. Interactive sessions and their evidence
live under `artifacts/console/`. See [the console guide](console.md) for the
Store/System lenses, what is measured versus illustrated, and the safety scope.

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
scrapes to match the completed run. It requires zero lag in all twelve partitions
and a Kafka exporter sample newer than SQL reconciliation. Persistent backlog,
incorrect counters or missing series still fail with their observed values.
To repeat verification without rebuilding or republishing, run
`./streamscale.sh verify`.

Open the URLs printed by the UI command. It prefers these loopback addresses:

- Grafana: http://127.0.0.1:13000/d/streamscale
- Prometheus: http://127.0.0.1:19090

If either default port is occupied, the command chooses an available port and
prints the actual URL. Existing listeners keep running. To choose ports explicitly:

```sh
./streamscale.sh ui --grafana-port 13001 --prometheus-port 19091
# Or let the operating system choose both ports:
./streamscale.sh ui --grafana-port 0 --prometheus-port 0
```

The same port options work with `./streamscale.sh all`. An occupied explicitly
chosen port produces a short error with the alternative command options.
If `M0–M8 passed` precedes a dashboard error, the checks and report generation
have completed; reopen access with `./streamscale.sh ui` without repeating the run.

View `artifacts/k8s/credentials.json` for Grafana's generated username/password.
In Grafana, choose the current run UUID from the Run ID dropdown. The verified
UUID and current cluster directory are printed by the launcher; metadata also
lives in `artifacts/k8s/deployment.json`. This is an analytics pipeline with
Grafana monitoring and SQL/artifact inspection. The M7 runner also produces a
pilot and 27 measured runs; M8 generates the analysis only once the full matrix passes.

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

M6's independent mode and replica-bound exercises run with `./streamscale.sh scaling`
or `make scaling`. See [scaling operations](scaling.md) for individual-mode commands.

M7 uses `./streamscale.sh experiments` (or `make experiments`). A first campaign
can take over an hour: each of 27 cells has a separate 30-second warmup, a
60-second publishing window, a drain and a controlled restart. Completed
campaigns are reused only after raw checksums and matrix invariants pass. An
interrupted execution is reconciled and excluded before its cell is retried.
Use `./streamscale.sh experiments --fresh` to retain previous evidence and
measure a new campaign. Raw evidence and CSV files live under
`artifacts/experiments/`; `latest.path` identifies the current campaign.
See [the frozen measurement protocol](experiments.md).

M8 uses `./streamscale.sh analysis` or `make analysis`; it requires complete,
audited M7 evidence and can run with the cluster stopped. The report, companion
CSV files, SVG/PNG charts and presentation PDF appear in `docs/results/current/`.
The plotting environment has its own locked dependencies and does not alter
the consumer image. See [reproduction](reproduction.md) and
[presentation steps](presentation.md).

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

`resume` also waits for the console agent and console consumer when the console
release is installed. `stop` stops them with the kind node and retains their data.

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

M6's complete launcher validation passed with 118 unit tests and 28 live
integration tests. Fixed peaked at one ready consumer; CPU-HPA and lag-KEDA
peaked at three, with successful reconciliation in every mode. See
[M6 evidence](m6-evidence.md) and `artifacts/m6-full-check.log`.

The final complete M0–M8 launcher passed on 2026-10-08 with 144 unit tests,
28 live integration tests and clean Ruff checks. It verified fresh M5/M6 runs,
fixed/CPU-HPA/lag-KEDA isolation with peaks of 1/3/3 ready consumers, the retained
27-run matrix and the regenerated M8 report/PDF. Its console log is
`artifacts/m8-full-check.log`. That acceptance left the fixed demo
`run-20261008131220-59df711a`, UUID `427bb2f5-e97c-4232-b6f1-03164fe2eedb`.
Every measured run reconciled and survived the controlled restart; the matrix
contains 249,048 unique facts. See [M7 evidence](m7-evidence.md),
[M8 evidence](m8-evidence.md) and [the report](results/current/report.md).

The dashboard port-conflict fix passed complete launcher acceptance on
2026-10-10 with 161 unit tests, 28 live integration tests, clean Ruff checks,
all three scaling modes and the retained 27-run audit. See
`artifacts/ui-fix-full-check.log`. Live checks with both default ports occupied
verified alternate URLs, Grafana/Prometheus health, and scoped Ctrl+C/SIGTERM
cleanup; evidence is in `artifacts/ui-port-conflict-check.json`. The acceptance
left fixed demo `run-20261010035945-18f5271f`, UUID
`fbd2fe2b-bf4d-46bb-8b6f-342a0d5d32ff`, with 396 facts, 200 states and zero lag.

The complete M0–M15 launcher, including the interactive console's live-cluster
and Chrome acceptance, passed on 2026-10-12 (07:02:21Z–07:32:18Z): 167 unit tests,
28 live integration tests, fresh M5 cluster verification, all three M6 modes, the
retained 27-run audit, M8 regeneration, and 138 + 19 console unit tests, 41 console
live-cluster tests and 9 Chrome tests including the full sale → overload → rescue
→ crash → outage → recovery → reconciliation journey. Log:
`artifacts/m15-full-check.log`; details in [M15 evidence](m15-evidence.md).
Running services were not stopped by these checks; the console's own agent and the
shared Kafka/PostgreSQL were scaled only by the failure lab's bounded, restored faults.

## ML Predictive (M16–M20)

One new scaling choice combines history-only quantile forecasts and predictive
capacity control. Its default p95 objective is 2 seconds within 1–12 replicas.
The predictor cannot see future schedules. Warm-up, fallback and capacity limits
are statuses within this mode. Bayesian policy tuning happens offline.

```sh
./streamscale.sh cluster
./streamscale.sh predictive-prepare   # live calibration, training and 12 tuning trials
./streamscale.sh predictive-check     # live forecast, override/release and reconciliation
./streamscale.sh predictive-demo      # one representative three-minute pilot
./streamscale.sh predictive-evaluate  # separate 36-run held-out comparison
./streamscale.sh console              # select ML Predictive and ML Predictive pilot
```

Preparation is a substantial one-time campaign, not a short startup check. It
records twelve capacity-calibration runs, twelve training runs, four validation
runs and two workloads for each of twelve policy candidates, plus drain time.
Subsequent invocations reuse compatible artifacts or resume at completed captures.
An interrupted capture is retained in console sessions; let it drain before
resuming. Training/tuning never use held-out evaluation runs. Close a running
console server with Ctrl+C before starting a campaign; the command refuses to
compete with it. A running sale is not stopped automatically.

Results and model/policy provenance live under `artifacts/predictive/`, separate
from historical `docs/results/current`. Models use checksummed local artifacts,
not user-uploaded pickle files. Runtime, feature or environment incompatibility
requires preparation again. If all tuning candidates miss the latency target,
the default policy is retained and the tuning report says `infeasible`.

`./streamscale.sh all --no-ui` includes these stages and the full predictive
comparison. Its first execution can take several hours. Performance improvements
must come from the held-out report; correctness and meeting the latency target
are separate outcomes. Existing stop/resume commands above retain every artifact.

## Using the refined console

Open the printed login URL from `./streamscale.sh console`. Use Overview to watch
the flow, Sale to plan/control traffic, Scaling to choose capacity, Failure lab
for bounded fault experiments and Sessions for replay/results. Existing controls
and artifacts remain available; detailed settings and histories expand on demand.

**Docs** contains searchable first-run instructions, UI usage, architecture and
correctness details, ML Predictive preparation, exact project commands and
troubleshooting. Command blocks have a Copy button. All five guided demonstrations
and the Store/System glossary live under **Docs → Guided demos**. Documentation
has no repeating metrics or sale-control bar; active fault recovery remains
available globally. After login, `#docs` opens the guide; `#docs/scaling` and
`#docs/commands` support direct links and browser back/forward navigation.

On phones all six destinations remain visible. The diagram's List view and the
component inspector provide readable details; Map remains available. View options
retains vocabulary, reduced motion and presentation. Selecting a component or
looking up a record opens the same keyboard-accessible inspector.

## Twelve-partition configuration (M21)

The maintained configuration uses **12 input and DLQ partitions**, starts at one
consumer and permits **1–12 replicas** in CPU-HPA, lag-KEDA and ML Predictive.
Fixed remains one; manual zero pauses processing. All comparisons use the same
partition count and per-consumer resource limits.

```sh
./streamscale.sh all --no-ui    # complete validation, preparation and comparison
./streamscale.sh capacity-check # twelve consumers, ownership, downscale and SQL
./streamscale.sh console        # Scaling: choose a mode or manually choose 0–12
```

For an existing three-partition installation, finish and drain every sale, close
publishers and pause consumers before bootstrap. `make bootstrap` explicitly
enables guarded expansion from 3 to 12 and applies SQL migration 0002. Expansion
retains records and offsets; never delete topics or volumes to upgrade. An active
group or changing source watermark blocks expansion. Do not resume an old
publisher after expansion: start a fresh run so each order keeps one partition
throughout its execution. Counts other than legacy 3 or current 12 are rejected.

The agent upgrades persisted legacy bounds and reapplies its selected controller
under the existing ownership lock. Historical reports and replay data remain
available. The launcher audits the completed historical M7 matrix even when its
code fingerprint differs; it explicitly labels that evidence historical. It
never resumes an unfinished campaign with incompatible code.

Preparation recalibrates **all twelve replica counts** and invalidates old models
through capacity and feature metadata. Higher replica limits do not promise
linear throughput: the database and local node can still constrain capacity.
The historical report under `docs/results/current` describes the old 1–3 range.
Current results belong to the separate fingerprinted predictive campaign.

The move from three to twelve provides a wider experimental range: three replicas
allowed only two upward steps, whereas twelve lets us inspect gradual scaling,
stabilization, manual rescue and predictive resource savings. Partition count
must grow with the replica ceiling so added consumers can receive useful work.
This does not guarantee linear throughput or a latency improvement; use the
measured evidence in [M21 acceptance](capacity-evidence.md).


Large twelve-consumer workloads use bounded agent backfill (32 MiB / 1,200 frames)
and streamed reconciliation with temporary disk-backed proof tables. The agent
has up to 1 GiB of transient `/tmp` space and retains its 768 MiB memory limit.
Session recordings, source records and SQL facts remain retained. Reconciliation
reconnects after agent/forward loss; its request identity prevents reuse of an
older result for a new check. See [capacity evidence](capacity-evidence.md) for
validation status and the failure that motivated this correction.
