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
