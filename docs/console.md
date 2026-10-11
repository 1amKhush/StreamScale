# Interactive console

The console is a local browser view and control surface for the real StreamScale
kind deployment. It explains the pipeline through one analogy: **a supermarket
checkout during a flash sale**. See [the execution plan](interactive-console-plan.md)
for the complete scope, the analogy table and where the analogy stops.

## Start it

```sh
./streamscale.sh console
```

The command builds the web assets, deploys the console release into the existing
`kind-streamscale` cluster, and prints a login link such as
`http://127.0.0.1:18080/?token=…`. Open that exact link. It also opens Grafana and
Prometheus forwards and links them. Ctrl+C closes the console server and its
forwards. The cluster, its data and the console's in-cluster agent keep running.

Requirements beyond the main project: Node.js 20.19 or newer with npm. The pipeline
must already be deployed (`./streamscale.sh cluster` or `all`). If port 18080 is
busy, an available port is chosen and printed. Choose one with
`./streamscale.sh console --port 18081`; dashboard ports accept the same
`--grafana-port`/`--prometheus-port` options as `ui`.

## Store and System lenses

The **Store lens** names components with the analogy; the **System lens** uses
Kafka, Kubernetes and SQL names. Both show the same live values, and the other
lens's name is always visible as a caption. Hover any component name for its
explanation and the point where the analogy stops being accurate.

| Store lens | System lens |
| --- | --- |
| Store doors | Interactive publisher |
| Checkout lanes 0–2 | Partitions 0–2 of `orders.events.v1` |
| Tickets issued / now serving / queue length | High watermark / committed offset / lag |
| Cashiers and the lanes they cover | Console consumer pods and their partition assignment |
| Floor manager | Scaling controller (fixed, manual, CPU-HPA, lag-KEDA) |
| Sales ledger | PostgreSQL `order_event` and `order_state` |
| Problem-items desk | Dead-letter topic `orders.events.dlq.v1` |

## The checkout floor (M10)

The centre of the console is a live map of the pipeline:

- **Store doors** (publisher): arrival rate, acknowledged events against the
  sale's plan, delivery failures, and a warning when the broker stops accepting.
- **Three lanes** (partitions): queue length (lag) as a belt filling up, its trend
  over five seconds, tickets issued (high watermark), now serving (committed
  offset) and the cashier that currently owns the lane.
- **Cashiers** (console consumer pods): status in plain words (serving, on standby,
  waiting, retrying, collapsed with its exit code, overwhelmed, leaving), lanes
  covered, and CPU as a share of the pod's *request* (the CPU-HPA's basis), with
  the share of its limit and memory beside it.
- **Floor manager** (scaling controller): policy, and wanted · present · at lanes
  (desired replicas · ready pods · consumer-group members), shown separately.
- **Sales ledger** (PostgreSQL) and **problem-items desk** (DLQ): stored facts and
  rate, the order status board, live DB p95, DLQ copies by reason.

Edges carry the measured rate as a label. Moving dots are a **sampled
illustration** (at most six per edge, scaled logarithmically), never one dot
per event; *Motion off*, or the operating system's reduced-motion setting,
removes them and keeps every number. Above the floor, plain-language signals
name a growing backlog, missing consumers, saturated consumers, a slow database
or an unavailable broker/database, each with the measurement behind it.

Below the floor, synchronized charts share one clock and crosshair: arrivals vs
served, queue by lane (lanes keep their map colours), live p95 wait, cashier
counts, CPU against the HPA target, DB write p95, problem items, and collapses
and recoveries. Each chart has a *Table* view, and vertical lines mark console
actions. Select any component for the inspector: lane offsets and ranges, a
pod's restarts, exit reasons and per-process counters, controller conditions,
the latest stored facts, the latest DLQ envelopes, and a lookup by order or
event ID that shows the broker acknowledgement, the ledger history, the latest
state and any DLQ reason.

Live p95 values are estimates from histogram buckets over 15 seconds. The exact
p95 comes from SQL timestamps once a sale has drained.

## Planning and controlling a sale (M11)

The **Sale** tab plans a sale before the doors open:

- **Presets** reuse the measured M7 workloads: *Flash sale* (15 s at 52/s, 20 s
  at 390/s, 25 s at 52/s, seed 202), *Steady*, *Overload and recovery*, plus an
  *Extended flash sale* with a one-minute rush for live demonstrations.
- **Schedule and seed**: up to twelve phases of 1–3,600 s at 1–2,000 events/s,
  at most one hour and 400,000 events. The same seed reproduces the same logical
  sale; every start gets a fresh run ID and consumer group.
- **Problem shoppers**: malformed, duplicate and late percentages (0–20%), seeded
  like the measured correctness profile (defaults 1%, 2%, 5%).
- **Estimate**: events, orders, expected DLQ items, duplicates and late events, and
  the queue one, two or three consumers would build at the M7 pilot's measured
  capacity (about 130 events/s each). It is labelled as an estimate.
- **Saved presets** live in `artifacts/console/presets.json`; *Run the last sale
  again* repeats its settings as a fresh run.

While a sale runs, the same tab (and the top bar) controls it:

| Control | What happens |
| --- | --- |
| Hold the doors (pause) | No new records are produced. Delivery confirmations keep arriving and consumers keep draining. |
| Reopen (resume) | The schedule clock continues from where it paused. |
| Set a rate | A live events/s rate replaces the schedule; *Follow the schedule* returns to it from the current position. |
| Close the doors and drain (stop) | Production ends, every sent record is flushed and acknowledged, and consumers drain the backlog. |
| Abort | Unsent records are discarded; records already sent finish and drain. The partial run is kept and reconciled. |

After the backlog reaches zero the console **reconciles the run against its
Kafka source range**, not against the plan, so stopped, aborted and interrupted
sales are judged on what was actually published: every acknowledged valid event
stored exactly once, nothing stored that is not in the log, latest state equal
to the log's ordering, every invalid source in the DLQ (copies counted
separately) and zero remaining backlog. It also reports the **exact SQL p50/p95/p99**
latency of stored facts. The result appears in the Sale tab and in
`artifacts/console/sessions/<session>/reconciliation.json`.

A session is *comparable* only if it completed unmodified (no pause, rate change,
scaling change or fault) and reconciled. Interactive sessions never enter the M7
results.

Recovery is part of the design: closing the browser changes nothing; restarting
the console server reattaches to the running or draining sale; if the in-cluster
agent restarts mid-sale, the run is finalized as `interrupted` from its durable
acknowledgement log and still drains and reconciles. Only one console server may
run at a time. Buttons that appear in place of the one just pressed ignore clicks
for 0.7 s, so a double click cannot pause a sale it just started.

## What is real

- Values come from the cluster. The in-cluster agent samples Kafka offsets and
  group membership, SQL counts, Kubernetes objects and each console consumer's
  metrics roughly once per second while someone is watching. It slows to every
  ten seconds when nobody is.
- Every source reports its own sample time. A failed source shows **—** and an
  error, never zero. The top badge says **Live** only while the browser stream is
  open, the agent answers and samples are under four seconds old. Otherwise it
  says **Stale**, **Console disconnected** or **Cluster agent unreachable**.
- Console runs never enter the measured comparison. They use the unchanged
  consumer image and resource limits, but a separate release, consumer group
  and runs directory (`/runs/console/`).

## Safety and scope

- The server binds to loopback and accepts only loopback `Host` headers. The login
  token becomes an HttpOnly, SameSite=Strict cookie. State-changing requests and
  the live stream also require a loopback `Origin`. Cluster credentials and the
  agent token never reach the browser.
- The agent's Kubernetes role names the exact resources it may change: the
  console consumer Deployment's scale, the console autoscalers, and the Kafka and
  PostgreSQL StatefulSet scale (used only for temporary outages). Pod restarts and
  CPU restriction are checked against the console consumer label in code.
- The console holds the existing cluster workflow lock while a sale is active, so
  cluster deployments, scaling exercises and measured campaigns cannot publish
  into its source range at the same time, and vice versa.
- Every action is a numbered operation in `artifacts/console/journal.jsonl`.
  Repeating a click reuses the original operation instead of running it twice.

## Checks

```sh
./streamscale.sh console-check   # or: make console-check
```

This runs console Python unit tests, web type checks and unit tests, the web
build, console deployment, live-cluster acceptance and Chrome browser acceptance.
It writes `artifacts/console/check.json`. `./streamscale.sh all` includes it.

| Target | Purpose |
| --- | --- |
| `make console-install` | Sync `console/.venv` and `npm ci` in `console/web` |
| `make console-web` | Type-check and build the browser assets |
| `make console-test` | Python and web unit tests |
| `make console-deploy` | Build the worker image on the deployed pipeline image and install the release |
| `make console-live` | Live-cluster acceptance only |
| `make console-serve CONSOLE_ARGS="--port 0"` | Serve the console without rebuilding |

## Files

| Location | Responsibility |
| --- | --- |
| `console/server/streamscale_console/` | Loopback FastAPI server, telemetry derivation, operations and journal |
| `console/worker/streamscale_console_worker/` | In-cluster agent, read-only observer, scoped Kubernetes client, console consumer wrapper |
| `console/deploy/` | Worker image (built on the deployed pipeline image) and the `streamscale-console` chart |
| `console/web/` | React/TypeScript browser console and Playwright browser checks |
| `console/tests/` | Unit tests and `live` cluster acceptance tests |
| `artifacts/console/` | Deployment record, journal, server login record (0600), check results |

## Troubleshooting

- **"Cluster agent unreachable"**: the console keeps retrying its port forward.
  Check `./streamscale.sh status`; after `resume`, the agent pod needs a few
  seconds. `artifacts/console/agent-forward.log` has forward errors.
- **"The StreamScale pipeline is not deployed"** from deploy: run
  `./streamscale.sh cluster` first.
- **Login page instead of the console**: open the full link printed by the
  command; it is also in `artifacts/console/server.json` while the server runs.
