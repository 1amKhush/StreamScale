# M15 evidence: end-to-end console acceptance

Started 2026-10-12T03:07Z, after M14 (`8ee87a0`). M15 code: `791e091` (journey,
lifecycle, launcher and documentation) and `3019693` (comparison ordering fix,
chart PNG export, dashboard links).

## Scope delivered

- **Browser journey** (`console/web/e2e/journey.spec.ts`), through the UI only:
  fixed mode → extended flash sale → overload signals and backlog over 2,000 →
  manual rescue to three cashiers, one lane each → crash after a DB commit,
  followed by its own fault ID to a verified single SQL fact → PostgreSQL outage
  shown on the floor and recovered from the top bar → close the doors, drain and
  reconcile with zero missing, duplicate or regressed facts → back to fixed.
- **Launcher lifecycle** (`console/tests/live/test_console_lifecycle.py`): the real
  `./streamscale.sh console` deploys and serves on an available port with working
  Grafana and Prometheus links, a second console server is refused, `status`
  lists the console pods, and Ctrl+C closes the console, its forwards and its login
  record and journals the stop.
- **Launcher**: help and `all` cover M0–M15; `all` runs `make console-check` after
  M8 and, without `--no-ui`, ends by serving the console with dashboard links.
- **Documentation**: run guide (first demonstration, checks), README, console
  guide (lenses, floor, sale controls, staffing, failure lab, sessions, guide,
  presenting, safety scope), plan status and per-milestone evidence.

## Defects found during M15 acceptance

1. A browser-side race left the console's login record in place briefly after
   Ctrl+C (`uv` exits once it has forwarded the signal). The server now removes
   its record before closing forwards, and the test waits for the shutdown.
2. The first full launcher run (2026-10-12T03:11:26Z) passed M0–M8 and 40 of 41
   console live tests, then failed: the formal comparison reported `completed`
   before its final switch back to fixed mode had finished. Restoration now
   precedes completion (`3019693`), and the launcher was run again in full.

## Acceptance

Final command, on `3019693`: `./streamscale.sh all --no-ui`, exit 0,
2026-10-12T07:02:21Z–07:32:18Z, ending with `M0–M15 passed`. Log:
`artifacts/m15-full-check.log`; console summary `artifacts/console/check.json`.

| Stage | Result |
| --- | --- |
| M0–M4 | local smoke and fixture demo passed; 167 unit tests; Ruff clean; 28 live integration tests |
| M5 | fresh cluster run `b4e1da90-b6cc-4e15-bc2a-5a4160ca954c` verified (396 facts, 200 states, 4 DLQ sources, zero lag, zero missing, zero regressions), with the console release installed |
| M6 | fixed, CPU-HPA and lag-KEDA each passed; peaks 1 / 3 / 3 ready replicas |
| M7 | retained 27-run matrix verified (campaign fingerprint unchanged) |
| M8 | audited report and figures regenerated |
| Console (M9–M15) | 138 Python and 19 web unit tests; build; deploy `streamscale-console:524efda6615f8172`; 41 live-cluster tests in 1,209 s; 9 Chrome tests in 286 s |

The browser journey's session (`session-20261012072858-345c03`, extended flash
sale with a scaling change, a crash after a DB commit and a PostgreSQL outage)
reconciled with 17,074 acknowledgements, 16,571 unique facts, zero missing
acknowledged IDs, zero duplicate stored IDs, zero state regressions and 176 invalid
sources each in the DLQ; exact SQL p95 35.1 s, and the session is correctly marked
not comparable.

The formal comparison in this run (`comparison-20261012071425-911fd5`, flash-sale,
all three runs eligible, 9,781 facts each):

| Mode | Exact SQL p95 | Peak lag | Most members | Replica-seconds |
| --- | ---: | ---: | ---: | ---: |
| Fixed | 32.7 s | 4,940 | 1 | 88 |
| CPU-HPA | 15.3 s | 4,925 | 3 | 150 |
| Lag-KEDA | 0.98 s | 153 | 3 | 193 |

In M14's comparison CPU-HPA had already scaled during the quiet phase (0.97 s); here
it scaled after the rush began. This variability between single sequential runs is
why console comparisons are descriptive, and why the audited 27-run matrix remains
the measured result.

Acceptance criteria:

- **Browser starts a sale, demonstrates overload, rescues it, injects and recovers a
  fault and reconciles SQL/DLQ**: `journey.spec.ts` passed in the full run (1.8 min).
- **Full maintained launcher passes**: `./streamscale.sh all --no-ui` exit 0 with
  every stage above; the earlier failing run and its fix are recorded above.
- **Committed evidence and copy-paste commands**: `docs/m9-evidence.md` …
  `docs/m15-evidence.md`, `docs/console.md`, `docs/run-project.md` and the README.

Known limits, stated rather than hidden:

- Console comparisons are single sequential runs on one shared local node.
- Backlog and latency targets for signals are fixed in code, not user-configurable.
- If the console server is closed during a sale, the sale continues in the agent
  but the cluster workflow lock is released; finish or abort sales before running
  `cluster`, `scaling` or `experiments`.
- Kafka and PostgreSQL outages affect every client of the shared broker and
  database, including the idle measured consumer; run `cluster` (or `all`) before a
  strict `verify` afterwards.
- Sessions recorded before M14 replay rates, lag and replicas exactly but lack
  offsets and lane ownership; the replay says so.
