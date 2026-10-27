# M16 engine preview — implementation evidence

Implementation and checks performed on 2026-10-27. Owner: coding agent.
Base repository revision: `6b7dcad` (master at checkout).

## Implemented

- Application-independent adapter protocol and versioned scenario validation.
- Kafka input, read-only parameterized PostgreSQL output and original-order bridge.
- Warmup isolation; paced load; bounded scheduled fault hooks; bounded output
  observation; exact identity/value checking; cleanup on failure/interruption.
- Planned/submitted/broker-acknowledged evidence and producer lateness validity.
- Machine-readable verdicts with 0/1/2/3 CI exits; immutable run directories,
  raw manifests, acknowledgement/sink evidence and file/source checksums.
- Seeded matched baseline/candidate repetitions, randomized execution order,
  unsuitable-run exclusion and descriptive median p95.
- Independent telemetry worker/reference checker and live regression acceptance.
- `streamscale.sh engine` / `engine-check`, Make targets, integration in `all`,
  documentation and a GitHub Actions Docker-backed acceptance job.

## Executed checks

| Command | Observed result |
| --- | --- |
| `uv sync --frozen` | Installed the existing pinned dependencies; no dependency changes |
| `uv run --frozen pytest -q` | 183 passed, 6 skipped, 29 deselected |
| `uv run --frozen ruff check .` | Passed |
| `uv run --frozen ruff format --check .` | Passed |
| `bash -n streamscale.sh` | Passed |
| `./streamscale.sh engine --help` | Displays run/compare command parser |
| Real launcher invocation using the memory adapter | Baseline exit 0/pass; lossy candidate exit 1/fail; two-repetition comparison exit 1/fail |
| `git diff --check` | Passed |
| `./streamscale.sh engine-check` | Blocked before services/publication: `Missing command: docker` |

Six chart tests were skipped because pinned Helm is not installed. The 29
integration cases were deselected by the repository's default pytest marker.
The new engine suite contains 20 test cases; launcher tests also cover forwarded
engine arguments and the new full-workflow target ordering using fake service
commands. These do not substitute for real Kafka/PostgreSQL acceptance.

The engine tests exercise missing outputs despite delivery acknowledgements,
mismatched values, unexpected/duplicate outputs, delayed duplicates after an
initial complete observation, negative timestamps, producer overload, failed
acknowledgements, warmup isolation, fresh IDs, preserved evidence, cleanup after
preparation/fault errors, fault execution in a phase's tail, comparison rejection
for mismatched inputs, unsuitable candidate exclusion, actual broker-callback
semantics at the adapter boundary, parameterized read-only SQL and order wire
compatibility. The CLI smoke uses the memory adapter only; it is not performance
evidence for Kafka or cloud infrastructure.

## Pending acceptance

No Docker executable/daemon is available in this execution environment. The live
independent-worker test and the complete M0–M16 `all` workflow were not executed.
Run `./streamscale.sh engine-check` on a Docker-equipped machine or inspect the
new GitHub workflow's live result. Live acceptance retains its artifacts under
`artifacts/engine/acceptance-*/` and requires normal-worker pass plus lossy-worker
failure against identical logical manifests.

M16 remains a preview pending live acceptance. The original order bridge covers
immutable facts; the original inspector remains responsible for its projection,
DLQ and replay checks. The general checker currently covers one-to-one outputs;
custom aggregate/fan-out/filtered semantics and large-scale streaming evidence
collection are follow-on work, not claimed complete here.
