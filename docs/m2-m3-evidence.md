# M2 and M3 acceptance evidence

Validated **7 October 2026 (Asia/Kolkata)**. UTC tracker transitions: M2 started
`2026-10-07T12:53:17Z`, locally validated `2026-10-07T13:03:43Z`; M3 recorded
in progress at `2026-10-07T13:03:43Z`, final checks passed by
`2026-10-07T13:20:28Z`. Qualitative scope estimates and dependencies are in
`milestones.md`; no sprint velocity or invented effort is recorded.

The observed M0/M1 commit is `4555e652e824ec3a0ab7c7de1af3e30bcb9fd228`.
M2/M3 changes remain uncommitted. Each published run records the observed Git
revision, dirty status and a SHA-256 fingerprint of source/schema/migration files.
No external tracker or design files were modified.

## M2 acceptance

| Criterion | Actual evidence |
| --- | --- |
| Same seed, same logical sequence | Two correctness manifests have identical logical SHA-256 and order IDs, but disjoint run/event IDs and distinct full manifest checksums |
| Fault schedule and ID behavior | 11 workload tests cover normal, malformed, duplicated, late and equal-time fixtures; per-attempt timestamps differ while logical duplicate fields remain equal |
| Exact amounts and scenario timing | Integer cents remain equal within each order; phase boundaries match the M0 schedules |
| Key/partition and acknowledged delivery | Live publisher compares every broker delivery partition with unsigned CRC32(key) modulo 3; raw acknowledgement logs preserve exact bytes and coordinates |
| Run registration and reproducibility | Run metadata registered before emission; manifest/acknowledgement checksums, seed/profile/scenario/mode, relative timing, actual timestamps and source start/end offsets preserved |
| Retained-data safety | Fresh run IDs/groups; existing directories and republishing refused; concurrent source writes make a run fail instead of silently entering comparison |

An initial seven-attempt M2 trial was successfully published and later drained and
inspected. Its checksum metadata was recorded when the acknowledgement format was
finalized, without changing its original event IDs or source offsets.

## M3 acceptance

| Criterion | Actual evidence |
| --- | --- |
| Valid fact and conditional state in one transaction | Real PostgreSQL test forces the state write to violate its constraint; neither fact nor state survives |
| History and latest state | Live fixture demo stores four unique facts and two PAID states; late creation cannot roll back state, equal-time terminal event wins by offset |
| Explicit commits after durable handling | Unit tests trace DB commit → partition offset commit and DLQ ack → partition offset commit; DB/DLQ failures never commit source offsets |
| Real crash-after-DB-commit replay | Consumer subprocess hard-exits 75 after one durable fact/state; broker offset still equals captured start; same-group restart finishes without duplicate facts or regression |
| Identity and routing conflicts | Changed logical fields under the same ID, partition changes and source-coordinate conflicts are rejected, with transaction rollback |
| Run isolation | Run-specific groups start from captured offsets; registration precedes publishing; SQL state keys include run ID |
| Duplicate/DLQ replay limits | Second group replays all seven fixture records, inserts no new facts and adds DLQ copies; unique invalid source coordinates remain two |
| Real task timestamps | Completion timestamp is set after global run drain and preserved during subsequent replay |

The crashing process's SQL inspection found exactly **one fact and one state**.
Its group's source offset remained at the captured start, below the fact's next
offset. Restart reported **3 newly accepted, 2 duplicates, 2 invalid**. Final
inspection reported **4 facts, 2 states, 0 missing IDs, 0 duplicate stored IDs,
0 state regressions**. The replay duplicate is expected under at-least-once
processing; neither Kafka transactions nor a cross-system transaction is used.

## Commands and results

| Command | Result |
| --- | --- |
| `uv sync --frozen --offline` | Packaged workload tool installed with existing locked dependencies |
| `uv lock --check --offline` | Passed; 19 packages resolved |
| `make test` | **86 passed**, 22 integration cases deselected |
| `make integration` | **22 passed**, 86 default cases deselected; final run 17.92s |
| `make check` | Lint passed; all 26 Python files formatted |
| `make demo RUN_DIR=artifacts/m2-m3-demo` | Published/drained/inspected seven attempts; final status passed |
| `uv build --offline` | Source archive and wheel built |
| Cold wheel install to a temporary directory | Consumer and workload modules imported from installed wheel; packaged schemas/migration available; seven-attempt manifest generated without checkout fallback |
| `git diff --check` | Passed |
| Docker Compose service inspection | Kafka and PostgreSQL remain healthy; local stack left running |

The agent used `UV_CACHE_DIR=/tmp/streamscale-uv-cache` for its sandbox and `.env`
for live connections. Integration writes used uniquely named PostgreSQL schemas
which were dropped afterward. Test broker records remain retained. No existing
development data, topic or Docker volume was reset or removed.

Live 200-order correctness run: **408 broker acknowledgements**, **396 accepted
unique facts**, **200 latest states**, **8 duplicate attempts**, **4 invalid/DLQ
sources**. All four fault codes were observed: `INVALID_JSON`, `INVALID_ENCODING`,
`INVALID_AMOUNT`, `KEY_MISMATCH`. Inspection passed with no state regressions.

The retained user-inspectable demo has run ID
`c64226be-afd7-4968-b601-1d8e1da02631`, group
`streamscale.run.c64226be-afd7-4968-b601-1d8e1da02631` and artifacts in
`artifacts/m2-m3-demo/`. Its original source fingerprint is
`dfbc56a2cc8f67c9b5366a6a5a8631d7347c96d198444c9b81714c461532ed41`.
The later completion-timestamp preservation fix was covered by the final suite.

```json
{
  "status": "passed",
  "acknowledged": 7,
  "expected_valid_unique": 4,
  "stored_facts": 4,
  "duplicate_attempts": 1,
  "duplicate_stored_ids": 0,
  "state_rows": 2,
  "state_regressions": 0,
  "invalid_attempts": 2,
  "dlq_copies": 2,
  "dlq_unique_sources": 2,
  "missing_valid_ids": [],
  "remaining_run_lag": {"0": 0, "1": 0, "2": 0}
}
```

## Review, limits and next work

Local review traced transaction boundaries, duplicate identity checks, per-order
serialization, partition/source-coordinate guards, explicit synchronous commits,
run floors/end offsets, broker acknowledgements, actual publish timestamps,
artifact checksums, retained data and installed-package behavior. It caught and
fixed a duplicate-ID path that could hide a conflicting source coordinate and
completion timestamps being overwritten by replay. A newly created group now
commits its captured run floor even for partitions containing no run records;
these are deliberate experiment boundaries, not acknowledgements of old runs.

The code-review skill was consulted. CodeRabbit CLI 0.7.0 remains signed out;
its remote review requires `coderabbit auth login` and was not run. Local review
and the runtime checks above passed without waiting for that optional service.

Basic acknowledged DLQ handling and bounded DB reconnects are included so mixed
fixtures can be processed correctly. M4's deliberate outage and rebalance fault
campaign is still next. M5–M8 deployment, metrics, autoscaling, controlled run
matrix and performance analysis are not implemented or claimed. The demo's
`--no-wait` and shortened workloads are correctness evidence, not speed rankings.
