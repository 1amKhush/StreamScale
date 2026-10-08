# Run M2 and M3 locally

Use the repository root and existing `.env`. For a new checkout, first copy
`.env.example` to `.env` without overwriting configured credentials.

```sh
uv sync --frozen
make up
make bootstrap
make demo RUN_DIR=artifacts/my-first-run
```

The demo registers a new run in PostgreSQL, publishes seven attempts, drains that
run's captured Kafka source range and reconciles facts/state/DLQ. It ends with
`status: passed`, four stored facts, two states, one duplicate attempt, two unique
DLQ source coordinates, zero missing valid IDs, zero duplicate stored IDs and zero
state regressions. A late creation cannot reverse the newer terminal state; an
equal-time terminal event wins by source offset. No measured speed comparison is
implied by `--no-wait`.

Keep `artifacts/my-first-run` to inspect the manifest, acknowledged payloads and
coordinates, configuration, consumer summary and inspection report. Use a **fresh
directory for every new execution**. Existing directories and attempted executions
cannot be overwritten/republished. No topic or database reset is necessary.

## Individual commands and a larger workload

```sh
uv run --frozen streamscale generate \
  --output artifacts/correctness-1 --seed 42 --orders 200 --profile correctness
uv run --frozen --env-file .env streamscale publish --run-dir artifacts/correctness-1
uv run --frozen --env-file .env streamscale consume --run-dir artifacts/correctness-1
uv run --frozen --env-file .env streamscale inspect --run-dir artifacts/correctness-1
```

This seed/profile gives 408 acknowledged attempts: 396 distinct valid IDs,
eight extra duplicates and four invalid records. Default publishing respects the
seeded schedule, including 10s delays. Add `--no-wait` to skip real waiting while
retaining the same logical ordering. Fault counts round down; small correctness
runs may have no faults. Use `fixtures` to guarantee all cases in seven attempts.

`consume --run-dir` subscribes using the run-specific group. On assignment it
initializes that group's offsets to the captured start after older retained data;
on restart it uses the later committed position. It rejects missing retained
records and mismatched run IDs inside the range. It commits only the handled
partition's explicit next offset, with automatic commit/store disabled. Reaching
the captured end stops the command; `--idle-timeout` bounds stalls (30s default).
`--batch-size` bounds fetched batches, and handling stays sequential in each
partition. `--max-records` stops after a partial drain for manual experiments.

For an unbounded input service, `consume --group GROUP --idle-timeout 3600` reads
registered runs until idle or Ctrl+C. A new generic group starts at the earliest
retained record; M1 smoke records have no run registrations and will block that
path. Prefer the bounded run command in this retained development stack. Never
use the generator or publisher inside the consumer service loop.

## Deliberately crash after the DB commit

Use a fresh run because the completed demo group has nothing left to replay:

```sh
uv run --frozen streamscale generate \
  --output artifacts/crash-1 --seed 42 --orders 2 --profile fixtures
uv run --frozen --env-file .env streamscale publish --run-dir artifacts/crash-1 --no-wait
uv run --frozen --env-file .env streamscale consume \
  --run-dir artifacts/crash-1 --crash-after-db-commit 1
```

The last command deliberately hard-exits **75**, after PostgreSQL commits the
first valid fact/state transaction and before that record's Kafka offset commit.
Restart with the same directory/group, without the injection flag:

```sh
uv run --frozen --env-file .env streamscale consume --run-dir artifacts/crash-1
uv run --frozen --env-file .env streamscale inspect --run-dir artifacts/crash-1
```

Kafka may wait about 10 seconds for the crashed member's session to expire.
Expected final facts/states remain four/two; the restarted process records two
duplicate outcomes (one deliberate duplicate plus one crash replay), while SQL
contains zero duplicate IDs. The snapshot retains the original group automatically.
The injection flag is for tests only; omit it in ordinary consumption.

To intentionally replay all source records through another group, add
`--group streamscale.replay.NAME` to both consume and inspect. Valid facts remain
unique; invalid records may produce additional DLQ copies. Inspection deduplicates
DLQ outcomes by source topic/partition/offset and reports both copies and unique
coordinates. This is at-least-once handling, not cross-system exactly once.

## Inspect SQL directly

Inspection emits run IDs and latest-state details, then stores `inspection.json`.
For SQL, use the run UUID from its output in this query:

```sh
docker compose --env-file .env -f deploy/local/compose.yaml exec postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

```sql
SELECT event_id, event_type, event_time, kafka_partition, kafka_offset
FROM order_event WHERE run_id = 'REPLACE_WITH_RUN_UUID'
ORDER BY kafka_partition, kafka_offset;

SELECT order_id, status, order_amount, last_event_id,
       last_event_time, last_kafka_partition, last_kafka_offset
FROM order_state WHERE run_id = 'REPLACE_WITH_RUN_UUID';
```

## Validation and next milestone

```sh
make test
make integration
make check
```

Integration tests use temporary schemas, execute an actual crashing consumer
subprocess, verify its unadvanced broker offset, and then drain/reconcile replay.
They preserve development business rows; test records remain in Kafka.

PostgreSQL connection failures get three attempts with bounded backoff. Exhaustion,
identity conflicts, partition changes, source coordinate conflicts or offset/DLQ
acknowledgement errors stop processing before a later offset is committed. Restart
the same group after resolving the cause. Changing logical fields while reusing
an event ID is an integrity failure, not a benign duplicate. Facts and state
changes share one transaction; late events retain facts without reversing state.

M4 remains the broader deliberate DB outage, restart/rebalance and DLQ fault
campaign. M5 adds deployment and metrics. No HPA/KEDA configuration, full benchmark
matrix, performance rankings or end-to-end exactly-once claim is made by M2/M3.

Implementation references: [Confluent Kafka Python commit API](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html)
and [PostgreSQL conditional conflict update](https://www.postgresql.org/docs/17/sql-insert.html).
