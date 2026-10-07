# M0 and M1 acceptance evidence

Validated on **7 October 2026, Asia/Kolkata**. Recorded state timestamps use UTC:
M0 started `2026-10-06T19:54:50Z`, validated `2026-10-06T19:59:51Z`; M1 started
`2026-10-06T19:59:51Z`, validated `2026-10-06T20:06:17Z`.

Baseline Git revision: `ee9ffddd3d90526c9f0d08e349987e2963441dbe`.
Changes remain uncommitted. The preexisting brief is preserved; its one-line
README title is expanded into setup and scope documentation.

## M0: executable contract

| Acceptance item | Evidence |
| --- | --- |
| Versioned event and DLQ contracts | Both Draft 2020-12 schemas checked by `jsonschema`; unknown fields rejected |
| Run/event IDs | UUID4 runs, UUID5 event IDs scoped to the run; 100 distinct IDs per run have no overlap across two runs; stable order IDs and pinned fixture derivation |
| Decimal/timestamp semantics | Exact amount boundaries; float/negative/overflow rejection; timezone, invalid-date and precision fixtures |
| Late events and tie handling | Seven committed ordering cases, later also evaluated by PostgreSQL on timestamp/offset tuples |
| DLQ codes and byte preservation | UTF-8/JSON/schema/version/type/amount/time/key errors tested; binary payload round trip and null-versus-empty contract |
| SQL plan | Three business tables, global event ID, run/source coordinate uniqueness, initial index policy, immutable history and transactional migration plan |
| Workload and measurement definitions | Reviewed local decisions in `contract-v1.md`; scenario seeds/schedules, fault proportions, windows, throughput/p95/lag/recovery and reconciliation rules |

Initial command `PYTHONPATH=src python3 -m pytest`: **59 passed**.
The same 59 passed after the locked environment was installed.
These are contract checks, not proof of an M2 generator or M3 service consumer.

## M1: local foundation

Test environment: Python **3.12.14**, uv **0.12.20**, Docker Engine **29.8.1**,
Compose **v5.5.1**. Locked dependencies include confluent-kafka **2.12.0**,
jsonschema **4.26.0**, psycopg/binary **3.3.3**, pytest **9.0.2**, Ruff **0.15.6**.
Image digests are recorded in `deploy/local/compose.yaml`.

| Command/check | Actual result |
| --- | --- |
| `uv sync --frozen` | Installed locked package and dependencies in `.venv` |
| `uv lock --check --offline` | Passed; 19 packages resolved |
| `make up` | Kafka and PostgreSQL healthy; ports bound to loopback |
| `make bootstrap` | Both topics verified with three partitions; `0001_initial.sql` applied |
| `make smoke` | Three broker acknowledgements, three identical consumed records across partitions 0/1/2; PostgreSQL committed decimal round trip `13.37` |
| `make test` equivalent (`.venv/bin/python -m pytest`) | **68 passed**, 16 integration cases deselected |
| `make integration` | **16 passed**, 68 default cases deselected |
| `make check` equivalent (Ruff lint/format) | Passed; all 14 Python files formatted |
| `git diff --check` | Passed |
| `uv build --offline` | Built source archive and wheel |
| Wheel install with `uv pip install --offline --no-deps --target …` | Both schemas and SQL migration loaded from the installed wheel, with no checkout fallback; reused locked dependencies |
| Manual Kafka console producer/consumer | Fixture ID `f42f6cb3-d54f-5a70-b91b-0adb2bd88bf4` received with exact key and payload at partition 0, offset 2; a fresh group joined before publication |
| `make down up bootstrap smoke` | Retained-volume restart healthy; migration result `applied: []`; fresh smoke passed at offsets `(0,3)`, `(1,2)`, `(2,2)` |
| Final PostgreSQL inspection | All three business tables contain **zero** rows; no leftover integration-test schemas; migration ledger retained |

All `make` and live Python commands used `.env`; the agent set
`UV_CACHE_DIR=/tmp/streamscale-uv-cache` for its sandbox. No database reset was
performed. Kafka retains the documented smoke/fixture records. The local services
are left running and healthy. `make down` stops them without deleting volumes.

First scripted smoke run:

```json
{
  "status": "passed",
  "run_id": "c879f1c8-bbee-4797-b6ca-20b346cb10c7",
  "topics": {"orders.events.v1": 3, "orders.events.dlq.v1": 3},
  "published": 3,
  "consumed": 3,
  "coordinates": [
    {"partition": 0, "offset": 0},
    {"partition": 1, "offset": 0},
    {"partition": 2, "offset": 0}
  ],
  "postgres_version": "17.7 (Debian 17.7-3.pgdg12+1)",
  "postgres_decimal": "13.37",
  "business_tables_written": false,
  "source_offsets_committed": false
}
```

Integration evidence covers repeat topic bootstrap, migration idempotency and
checksum refusal, transactional rollback of failing DDL, globally unique event
IDs and unique source coordinates, immutable history, state isolation by run,
all seven PostgreSQL ordering cases and the exact four planned history indexes.
Default tests also verify partition/configuration drift refusal and CLI errors
without credential leakage. Test database writes occurred only in an isolated
schema which was dropped after the checks.

## Defects and review

Local review traced schema parsing/format checks, key routing, migration transactions,
topic drift, acknowledged delivery, retained offsets, wheel assets and setup commands.
Found and corrected during implementation: valid-looking timezone offsets such as
`+00:60` being normalized by Python instead of rejected; regex end anchors allowing
trailing newlines; a draft statement counting two order events as three; and two
lint formatting errors. Final lint/format and runtime acceptance checks pass.
The initial editable install ran before the migration asset directory existed;
it succeeded once the planned SQL asset was added. An offline isolated wheel
resolution lacked registry metadata; direct wheel installation with the locked
environment verified its assets instead. These were build/check sequencing or
environment issues, not hidden runtime acceptance failures.

The code-review skill was consulted. CodeRabbit CLI **0.7.0** is installed but
`coderabbit auth status` reported **signed out**; remote automated review was
not run. It requires `coderabbit auth login`. Local review and the checks above
were completed without waiting for that optional service.

The older TeX designs and Group 6 tracker/change note were absent from this
workspace. Their content and external task states have not been reviewed or
changed. The supplied coding brief is the authoritative local baseline.

## Next milestone

M2: implement the seeded workload generator and publisher, persist its logical
manifest and acknowledgement log, and verify reproducible fault positions,
run-specific event IDs and key routing. No M2–M8 implementation or measured
performance result is claimed here. M3 adds the continuously running consumer;
its transaction/offset replay behavior remains to be implemented and tested.
