# M4 acceptance evidence

Implemented and exercised on 2026-10-08 UTC against the retained local
Kafka 4.1.2 / PostgreSQL 17.7 stack. Start transition: 2026-10-07T21:08:28Z.

`uv run --frozen --env-file .env pytest -m integration` first produced 26 passes
and one DLQ delivery-error failure. The fault exposed a native `KafkaError`
comparison to `None`; identity checking corrected it. After that change,
`pytest -m integration tests/integration/test_faults.py` passed all five live
fault cases in 41.35 seconds. The other 22 foundation/pipeline cases passed in
the full run. A focused unit regression also exercises the native error object.

Observed evidence:

- Refused DB endpoint: three attempts, increasing 0.2/0.4-second backoff,
  no facts, no DLQ, and group offsets still at the captured start. Recovery
  and same-group restart reconciled successfully.
- Terminated PostgreSQL backend: a fresh connection committed one fact; a
  second write was a duplicate.
- Hard exit 76 after a DLQ ack: invalid source remained uncommitted;
  restart yielded three copies for two invalid sources, four facts and no
  state regression.
- A second member joined a live backlog: partition assignments were disjoint
  and covered all three partitions. After the first member left, the survivor
  owned all three and drained the run: 800 unique facts, 400 states, zero missing
  acknowledged IDs and zero state regressions.
- Unavailable DLQ broker: native delivery timeout caused the expected failure;
  invalid source offsets did not advance. Recovery reconciled successfully.

Existing M3 tests separately verify exit 75 after DB commit, transactional
rollback, duplicates, late events, equal-time offset ties and full replay.
Test DB schemas were dropped; the user's existing data and volumes were retained.

Local review covered commit order, revocation/loss, bounded retries and shutdown.
CodeRabbit CLI is installed but signed out, so remote review was unavailable.
No benchmark or scaling result is claimed. Next milestone: M5 deployment/metrics.
