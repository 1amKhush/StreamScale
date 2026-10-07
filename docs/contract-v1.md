# Executable contract v1 (M0)

The supplied `STREAMSCALE_CODING_AGENT_CONTEXT.md` is the requirements baseline.
This repository initially contained only that brief and a one-line README.
The referenced TeX designs, Group 6 tracker and change note were not supplied;
their contradictions cannot be audited here. This decision record governs
implementation until those sources are reconciled. No external tracker is edited.

## Wire format and identity

`schemas/order-event.v1.json` is JSON Schema Draft 2020-12. All eight fields
are required, with no unknown fields. The executable checker also rejects
non-UTF-8, null payloads, duplicate JSON members and non-finite JSON numbers.
UUIDs and Kafka keys use lowercase canonical UUID text. `schema_version` is
the literal JSON integer `1` (booleans and `1.0` are rejected).

Amounts are **strings**, e.g. `"13.37"`, never JSON floats. They have exactly
two decimal places, no sign or leading zeroes, and range from `0.00` through
`9999999999999999.99`, matching PostgreSQL `NUMERIC(18,2)` without rounding.
There is no currency conversion; one experiment uses one nominal currency.

Timestamps use a timezone-aware RFC 3339 subset: uppercase `T`/`Z`, full
seconds, optional 1–6 fractional digits, or an explicit `±HH:MM` offset.
Reject impossible dates, leap seconds and unknown timezone offset `-00:00`.
Normalize instants to UTC when comparing; PostgreSQL stores `TIMESTAMPTZ`.
The precision bound prevents silently collapsing distinct event times.
Do not reject late events or require `event_time <= produced_at`: business
timestamps and publishing clocks are separate. Report clock skew in measurements.

Each execution gets a new UUID4 `run_id`. Distinct logical event index `i`
gets `UUID5(run_id, "streamscale:v1:event:{i}")`. A duplicate reuses the
original logical index and event ID. This is practical UUID uniqueness, not
a mathematical proof of no collisions; the database primary key is the guard.
Stable order index `j` gets
`UUID5(NAMESPACE_URL, "streamscale:v1:seed:{seed}:order:{j}")` across runs.
Seeds are integers in `[0, 2**63 - 1]`; logical indices are nonnegative integers.
The helpers in `src/streamscale/ids.py` freeze these rules without implementing M2.

The same scenario/seed reproduces order sequence, types, exact amounts,
relative business timing, publish scheduling and fault positions. Anchor the
relative event timeline to each execution's start. Set `produced_at` immediately
before each actual publish attempt, including deliberate duplicate attempts.
The original accepted fact retains its first accepted `produced_at`; identity
comparison excludes this per-attempt field. Reusing an event ID with different
logical fields is an integrity failure: stop the run, preserve the offset and
investigate; do not silently classify it as a normal duplicate.

Publish to `orders.events.v1` with `order_id` bytes as the key. Exactly three
partitions, replication factor one locally. Pin librdkafka's `consistent`
partitioner (unsigned CRC32 of the nonempty key modulo partition count) in
all future publishers. Disable topic auto-creation and refuse partition drift.
The DLQ `orders.events.dlq.v1` also uses three partitions locally.

## Persistence, late events and failures

`db/migrations/0001_initial.sql` will create the three business relations:

| Table | Key and purpose |
| --- | --- |
| `experiment_run` | UUID `run_id`; mode/scenario/seed, timestamps, Git revision, JSONB configuration |
| `order_event` | Global `event_id` primary key; unique `(run_id, kafka_partition, kafka_offset)`; immutable typed facts and accepted JSONB |
| `order_state` | Primary key `(run_id, order_id)`; status, amount, last event/time/partition/offset, update time |

Migration runner: apply sorted versioned SQL once under a transaction and advisory
lock, with a checksum ledger; refuse edits to an applied migration. Bootstrap
explicitly after PostgreSQL health checks, including on retained volumes. Primary
and unique indexes plus `(run_id, event_time)` and `(order_id, event_time DESC)`
are the only initial indexes. No further indexes without `EXPLAIN` evidence.
Facts reject `UPDATE`/`DELETE`; deliberate experiment reset may use `TRUNCATE`.
The future experiment runner must register `experiment_run` before publishing.
An absent run registration is an orchestration/storage failure, not malformed
input; preserve the source offset while resolving it.

In M3, insert a new fact and conditionally upsert state **in one transaction**.
Apply only when incoming `(event_time, kafka_offset)` is greater than stored.
An older timestamp never wins even at a higher offset; equal timestamps use
the higher offset. Compare instants, not timestamp strings. Each order must
remain on one partition. Late facts are retained. Duplicate IDs create no new
fact or state update. Status mapping is `ORDER_CREATED → CREATED`,
`PAYMENT_CONFIRMED → PAID`, `ORDER_CANCELLED → CANCELLED`; no workflow gates.

Commit each partition's Kafka next offset only after its contiguous records
are durably handled: DB transaction committed for valid events, or DLQ broker
acknowledgement for invalid events. DB outages pause/retry with bounded backoff,
never enter malformed-input DLQ. Crash/rebalance may replay. This is at-least-once,
with no Kafka/PostgreSQL cross-system transaction or exactly-once claim.

`schemas/dlq-envelope.v1.json` retains original bytes as base64; `null` means
absent, `""` means empty. Include source topic/partition/offset, bounded detail,
failure timestamp and one of these frozen codes (in decoding/validation order):

| Code | Condition |
| --- | --- |
| `INVALID_ENCODING` | Payload cannot decode as UTF-8 |
| `INVALID_JSON` | Missing, malformed or ambiguous JSON |
| `SCHEMA_VALIDATION_FAILED` | Wrong shape, missing/unknown field, UUID or field type |
| `UNSUPPORTED_SCHEMA_VERSION` | Positive integer version other than 1 |
| `UNSUPPORTED_EVENT_TYPE` | Unsupported string type |
| `INVALID_AMOUNT` | Noncanonical, negative, imprecise or out-of-range amount |
| `INVALID_TIMESTAMP` | Invalid instant, timezone or precision |
| `KEY_MISMATCH` | Absent key or bytes differ from canonical order ID |

After object decoding, unsupported version/type, amount and timestamps are
checked before remaining structural constraints; key check is last. Fixtures
freeze precedence for single faults. Multi-fault input receives the first error.
DLQ replay after acknowledgement can duplicate envelopes; reconcile by source
coordinate and preserve raw copies. Do not promise unique DLQ entries.

## Workload and measurement definitions for later milestones

Modes are `fixed` (1), `cpu-hpa` (1–3), `lag-keda` (1–3), mutually exclusive.
All use identical images, resources, topics, database and logical seed workload.
One pilot precedes 27 measured runs: three modes × three scenarios × three repeats.

Initial pilot parameters below are frozen comparison inputs, **not measured capacity**.
Rates count distinct logical events before fault injection; publish extra duplicate
attempts separately. Use 30s fault-free warm-up, then a fresh run ID/group for the
measured window. Each run/group starts from captured source offsets; warm-up
records never enter its measurements. Drain before starting another run.

| Scenario | Measured schedule | Seed |
| --- | --- | --- |
| `steady` | 120s at 100 events/s | 101 |
| `flash-sale` | 30s at 100/s, 15s at 1500/s, 75s at 100/s | 202 |
| `overload-recovery` | 60s at 1500/s, 60s at 100/s, then no publishing while draining | 303 |

Pilot may revise rates/windows before measured runs; version and record changes,
then hold them identical across modes. Recovery drain timeout starts at 300s.
Correctness profile: independently seeded 1% malformed attempts, 2% extra
duplicates, 5% delayed valid events (10s extra delay preserving business time).
Fault subsets may overlap except malformed and valid; snapshot the exact logical
manifest, positions, distribution algorithm and publisher acknowledgement log in M2.
Two events per order: created, then paid or cancelled in a seeded 80/20 split;
amount is stable within an order. Include equal-time and late-time fixtures.

Throughput = accepted distinct valid IDs ingested inside the half-open measured
window `[measurement_start, measurement_end)` / that window's seconds;
also report processing-window and drain-inclusive throughput explicitly.
p95 end-to-end latency = ingestion time minus original accepted `produced_at`
for unique measured valid events, in seconds. Preserve raw samples and define
the empirical nearest-rank percentile; do not average run percentiles. Prometheus
histogram p95 is separately labeled an estimate. Lag per partition = log-end
next offset minus committed next offset, captured independently from Kafka;
maximum lag is the peak sum across partitions (1s sampling). Recovery time
starts at overload-to-recovery transition and ends when lag is zero for five
consecutive 1s samples. Record censored runs at timeout. Scaling response is
first additional ready replica after load transition; replica-seconds integrates
ready replicas over time. Capture CPU/memory and raw series with intervals.

Snapshot run/group IDs, mode/scenario/seed, image digest/Git+dirty state, resource
limits, topic config, windows and UTC start/end. Metrics labels are bounded to
run/mode/scenario/result; retire per-run label sets, never label by event/order ID.
CSV is keyed by run ID. Reconcile acknowledged valid IDs, accepted distinct IDs,
extra duplicates, invalid attempts and deduplicated DLQ source coordinates.
Exclude runs with missing acknowledged valid IDs, duplicate stored IDs or state
regression after controlled restart/drain. Never infer backlog from PostgreSQL
or rank unreconciled runs. None of these benchmark results exists at M0/M1.
