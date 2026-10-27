# Generalized engine — M16 preview

StreamScale can now drive an application through an adapter rather than requiring
its order consumer or order database schema. This preview targets real staging
execution, not simulation or prediction of production capacity.

The first independent example is a telemetry worker: Kafka input -> integer
Celsius-to-Fahrenheit conversion -> PostgreSQL `telemetry_reading`. The worker
imports no StreamScale modules. Its input envelope and database schema are
separate from the original order contract.

## Acceptance and entry points

```sh
./streamscale.sh engine-check
```

This starts/reuses only the existing `streamscale-local` dependencies, runs the
independent worker normally, then runs a deliberately lossy version against the
same seeded logical workload. Acceptance requires the normal version to pass and
the lossy version to fail despite acknowledging its Kafka inputs. Topics, rows,
and evidence are retained; results are under `artifacts/engine/acceptance-*/`.
The acceptance harness stops only the worker subprocesses it created.

`./streamscale.sh all --no-ui` includes this acceptance after local checks and
preserves all the previous cluster, scaling, analysis and console stages. The
GitHub `Engine acceptance` workflow runs unit/lint checks and this live acceptance
on a Docker-equipped runner; its result must be inspected before declaring M16
fully verified. `make engine-test` runs the engine tests without Docker.

For an already running application:

```sh
./streamscale.sh engine run examples/telemetry/scenario.json \
  --output artifacts/engine/my-first-run
```

The user supplies the Kafka topic, a running worker, its own output table, and
connection environment variables. `engine run` does not provision or redeploy
applications. The included scenario assumes topic `streamscale.telemetry` and
table `telemetry_reading` already exist. The acceptance command provisions its own
unique test topic and example table automatically.

Set `DATABASE_URL` and `KAFKA_BOOTSTRAP_SERVERS` in the invoking environment.
`engine` deliberately does not load the local demo `.env` or start local services,
so it can target staging without silently selecting the demo. Use environment
variable names in adapter options, not inline credentials. Scenarios and custom
Python adapters are trusted executable test configuration: import paths execute
Python, SQL runs against your database, and hooks run commands as the invoking
user. Keep secrets out of scenario files, hook arguments and payloads; raw
scenario/payload data is preserved in artifacts.

## Scenario and adapter contract

See `examples/telemetry/scenario.json` for an executable configuration. A scenario
has `version: 1`, seed, adapter factory `module:Class`, adapter options, a nonempty
user-declared target description, payload fixtures and `{seconds, rate}` phases.
The seeded generator selects payloads from the fixtures. Relative timestamps and
logical payloads match across runs; run IDs and event IDs are fresh UUIDs.

The KafkaSQL adapter sends a JSON envelope with `run_id`, `id`, `index`, `payload`,
`produced_at` and `at_seconds`. The runner records submission timing separately.
Users with an existing wire protocol implement `send` to map this envelope to
that protocol. The target need not use StreamScale's consumer or order schema.

An adapter implements:

- `prepare(context)`: connect/check readiness without discarding retained data.
- `send(record, acknowledge)`: enqueue input; invoke the callback only on actual
  delivery confirmation with `id`, nullable `error`, and optional source coordinates.
- `poll(seconds)` and `flush(seconds)`: service callbacks; flush returns pending count.
- `observe(context)`: return run-scoped rows with `id`, JSON-compatible `value`,
  and timezone-aware ISO `completed_at`. Return every row, including duplicates.
- `close()`: release resources, including after partial preparation. Adapter I/O
  must have bounded timeouts. Callbacks run on the runner thread during send/poll/flush.

The built-in KafkaSQL output query is parameterized with `%(run_id)s` and runs in
a read-only PostgreSQL transaction with a statement timeout. It must alias output
columns as `id`, `value` and `completed_at`. The default checker expects exactly one
output per acknowledged input and compares its value to the input payload.
`expected: module:function` transforms each input payload into its expected output
value. It must be pure and independently implemented, not call the worker's
implementation. The telemetry reference lives in `examples/telemetry/oracle.py`.

This initial checker covers one-to-one transformations. Aggregations, intentional
filtering, fan-out, DLQ policies, and arbitrary business invariants need a subsequent
checker interface; they are not silently treated as missing-event failures in a
claimed generic compatibility mode. Use an application-specific adapter only when
its normalized output preserves this contract.

## Phases, timing and verdicts

Optional warmup runs as a separate execution with its own ID, manifests and
reconciliation. A failed warmup prevents measured publication. Measured execution
runs prepare -> load (with optional fault) -> drain/observation -> validation ->
cleanup. All measured phases are timestamped. Cleanup runs on exceptions and
handled interruptions, including failed preparation or fault commands.

Fault hooks are explicit and bounded, not an automatic cloud fault catalogue:

```json
{
  "hooks": {
    "fault": {"at_seconds": 1, "timeout_seconds": 10, "argv": ["./my-scoped-fault.sh"]},
    "cleanup": {"timeout_seconds": 10, "argv": ["./restore-my-target.sh"]}
  }
}
```

A `before_load` hook is also available for target-specific setup. Hooks receive
`STREAMSCALE_RUN_ID` and `STREAMSCALE_RUN_DIR`; no shell interpolation is used.
Fault commands run in a background thread during publication. Cleanup follows
fault completion or timeout; a failed cleanup makes the run an error. Hooks are
user-provided: they must explicitly undo their own faults. SIGKILL and machine
failure cannot guarantee cleanup.

The load schedule uses a monotonic clock and honors the full last phase, including
faults scheduled after the last input. Delivery confirmation is distinct from
submission. Evidence records planned/submitted/acknowledged counts, confirmations
inside the scheduled window, submission lateness, and the achieved timely fraction.
Producer queue saturation and missing/failed/duplicate delivery callbacks cannot
produce a passing load verdict. `max_lateness_seconds` (default 0.5) and
`min_achieved_fraction` (default 0.95) specify the accepted generator deviation;
choose them for the intended rate and window.

Observation continues for `drain_seconds` after publication/acknowledgement and
fault completion, even after the first complete snapshot, to detect delayed
outputs and duplicates. This is a bounded observation window, not proof that
nothing can arrive afterward. Zero broker lag is never used as output completion.
The final snapshot determines identity/value correctness. The first complete
snapshot provides `completion_after_load_seconds` measured from drain start;
this is a sampled post-publication completion interval, not fault recovery time.
`drain_seconds` in a result is the observation duration, distinct from the
optional completion-time threshold of the same name in `thresholds`.

p95 uses the nearest rank of per-output completion minus input `produced_at`,
including drained outputs. Incomplete or invalid-timestamp runs have no complete
p95. Cross-host clocks must be synchronized; negative, absent or naive timestamps
make the result inconclusive. Optional `thresholds.p95_seconds` and
`thresholds.drain_seconds` fail complete runs that exceed their limits.

| Status | CLI exit | Meaning |
| --- | ---: | --- |
| pass | 0 | Offered load valid, outputs correct, timing usable, thresholds met |
| fail | 1 | Valid experiment found incorrect outputs or a threshold breach |
| error | 2 | Configuration, transport, adapter, hook or cleanup execution failed |
| inconclusive | 3 | Offered load/delivery/timing unsuitable, or warmup did not pass |

Diagnostics remain in the result even when generator validity makes a run
inconclusive. Only passing executions are eligible for performance comparison.

## Matched baseline/candidate campaigns

```sh
./streamscale.sh engine compare \
  --baseline baseline.json --candidate candidate.json \
  --repetitions 3 --output artifacts/engine/comparison-001
```

Both target applications must be available, or their `before_load` hooks must
select the intended version. Comparison does not deploy either version itself.
Use distinct adapter environment-variable names for different target connections.

The engine rejects mismatched logical workloads, expected functions, measurement
windows, load tolerances, thresholds and fault definitions before execution.
Runs are sequential, with seeded randomized ordering, fresh IDs and retained
failed evidence. Each target is responsible for isolating its state by run ID;
shared caches and retained database growth can still affect performance.
`comparison.json` records the actual order, run IDs, statuses, eligible counts and
median p95. It withholds a complete-comparison verdict if any planned repetition
is unsuitable. These are descriptive samples, not significance tests. Matching
configuration does not prove the two real environments are equivalent.

Artifacts include the scenario, logical manifest, actual sent manifest, broker
acknowledgements, timed output snapshots and `result.json` with file checksums,
engine Git revision/dirty state/source hash, adapter/checker source hashes, and the
declared target identity.
An output directory must not already exist; earlier runs are never overwritten.
Target provenance is supplied by the caller, not independently discovered.

## Original order application

`streamscale.engine.adapters:Orders` bridges the existing order consumer to the
new runner; `examples/telemetry/orders.json` is its configuration. Bootstrap the
existing order schema first and run the normal consumer in a separate terminal:

```sh
# Terminal 1, from the repo root, after ./streamscale.sh local
uv run --frozen --env-file .env streamscale consume --stay-running --group engine-orders
# Terminal 2; use your environment manager to load the same connection variables
./streamscale.sh engine run examples/telemetry/orders.json --output artifacts/engine/orders-001
```

The bridge registers a fresh run and checks immutable order facts. It currently
creates one order per event; the original inspector and campaigns remain the
coverage for latest-state ordering, malformed inputs, DLQ semantics and replay.
Existing application code, migrations, console and M0–M15 commands retain their
contracts. This adapter is a compatibility bridge, not a claim that the legacy
experiment campaign has already been rewritten onto the new engine.

## Explicit limits and next acceptance

The memory adapter is for engine tests only. It is never cloud capacity evidence.
This preview stores plans, acknowledgements and output snapshots in memory/disk
and reads the run's complete SQL output on each observation. Large campaigns need
streaming reconciliation and bounded evidence collection before capacity claims.
There is no automatic bottleneck diagnosis, distributed generator, cloud cost
model, topology discovery or production guarantee.

M16 remains pending live acceptance until the Docker-backed independent example
passes. Further work should first exercise two independently maintained real
applications and improve their connection/checker ergonomics, then add telemetry
and bounded streaming evidence, rather than expanding cloud-provider coverage.
