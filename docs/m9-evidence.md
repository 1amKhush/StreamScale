# M9 evidence: console foundation

Started 2026-10-11T20:44:10Z (first console source file). The final acceptance
run passed at 2026-10-11T21:07:17Z on branch `interactive-console`.

## Scope delivered

- Separately locked dependencies: `console/pyproject.toml` + `console/uv.lock`
  (FastAPI 0.142.2, uvicorn 0.53.0, websockets 17.1, the root `streamscale`
  package by path) and `console/web/package-lock.json` (React 19.2.8, Vite 7.3.7,
  TypeScript 5.9.3). The root `pyproject.toml`/`uv.lock` are unchanged.
- In-cluster agent (`streamscale-console` Helm release) with a read-only observer:
  Kafka watermarks/committed offsets/group membership, SQL counts and probe
  latency, Kubernetes deployment/pods/autoscalers/infrastructure, Metrics API
  usage, and direct console-consumer metric scrapes. Each source has its own
  sample time and error.
- Console consumer Deployment running the unchanged `consume()` loop for the
  current console run, idle until M10/M11 sales exist.
- Loopback FastAPI server: login-link cookie, Host/Origin guards, `/api/state`,
  WebSocket telemetry stream, serialized journaled operations with idempotency
  keys, workflow-lock status from `/proc/locks`.
- Read-only browser shell with Store/System lenses, per-source freshness, an
  overall Live/Stale/Disconnected/Unreachable badge and the action timeline.
- Launcher: `console`, `console-check`; `all` runs console acceptance and ends by
  serving the console with dashboard links. Make targets `console-*`.

## Additivity check

The M7 campaign fingerprint was recomputed after the change:
`fingerprint matches retained campaign: True complete`. No file under `src/`,
`tools/workload/`, `schemas/`, `db/migrations/`, `deploy/k8s/` or the experiment
runner changed, so `./streamscale.sh all` still reuses the audited matrix.

## Acceptance

Command: `make console-check` (exit 0; log `artifacts/console/m9-console-check.log`,
summary `artifacts/console/check.json`).

| Stage | Result |
| --- | --- |
| Console Python unit tests | 69 passed |
| Web type check / unit tests / build | passed / 7 passed / built |
| Deploy console release | `streamscale-console:c6ab2bb70a6abaca` on `streamscale:dba6e7e963183912` |
| Live cluster acceptance | 15 passed |
| Chrome browser acceptance | 3 passed |

Acceptance criteria:

- **Real cluster data loads.** The live test compares the console's partition
  list, console pods, deployment replicas and Kafka/PostgreSQL readiness with
  `kubectl`. The browser test sees the Live badge and three checkout lanes.
- **Disconnected/stale states are explicit.** Scaling the agent Deployment to zero
  made the console report `unreachable` while keeping the last values and adding
  no new samples for four seconds. After restoring it, the console re-established
  its forward and resumed sampling. Browser tests closed the stream (badge
  "Console disconnected · reconnecting") and withheld samples (badge "Stale").
- **Cross-project targets are rejected.** Unit tests show the agent refusing to
  delete or resize non-console pods, scale anything but the console Deployment
  within 0–3, or write autoscalers with other names or targets. The live test
  asks the API server (`kubectl auth can-i --as` the agent's service account):
  it may scale `streamscale-console-consumer` and `streamscale-postgres`, but not
  `streamscale-consumer`, Grafana, the main autoscalers, secrets or `kube-system`.
  The agent returns 401 without the console token, and the console rejects
  unknown operations such as `cluster.delete`.

Also verified: `make check` (Ruff clean), `make test` (167 passed), and 21
launcher tests including the new console commands. Running services were not
stopped; the only service scaled was the console's own agent during its test.
