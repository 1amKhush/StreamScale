# Local foundation (M1)

Run from the repository root. Copy `.env.example` to the ignored `.env`, then
`uv sync --frozen`. `uv.lock` pins direct/transitive Python dependencies and
artifact hashes. Python minor version is fixed to 3.12. Compose pins official
Apache Kafka 4.1.2 and PostgreSQL 17.7-bookworm by digest. These are tested local
versions, not a claim that they are the latest releases.

```sh
make up
make bootstrap
make smoke
```

The equivalent bootstrap commands are:

```sh
uv run --frozen --env-file .env streamscale topics
uv run --frozen --env-file .env streamscale migrate
uv run --frozen --env-file .env streamscale smoke
```

Kafka uses KRaft with one combined broker/controller. The internal listener is
`kafka:9092`; host clients use `127.0.0.1:19092`. PostgreSQL listens internally
at `postgres:5432`, externally at `127.0.0.1:15432`. Change `KAFKA_PORT` alongside
`KAFKA_BOOTSTRAP_SERVERS`, or `POSTGRES_PORT` alongside `DATABASE_URL`.
Database credentials in `DATABASE_URL` must match the `POSTGRES_*` values.
The sample credentials are for this loopback development stack only.

`orders.events.v1` and `orders.events.dlq.v1` each have exactly three partitions,
replication factor 1, delete retention of seven days and no compaction. Startup
does not auto-create topics. Bootstrap verifies retained topics and fails on
partition/replication/config drift rather than expanding or deleting them.

Migrations are explicit, never dependent on first-start init scripts. PostgreSQL
has persistent storage at `/var/lib/postgresql/data`; Kafka also has its own
named volume. Repeat bootstrap after restart. Applied SQL checksums cannot change;
add a later migration. A failed migration rolls back its DDL and ledger changes.
The connection user in this milestone owns its local schema; a restricted runtime
role should be added before deploying the M3 service.

## Manual Kafka publish and consume

This independently exercises the broker CLI. It uses a distinct smoke UUID and
does not require a running service consumer. Start this command in terminal A
**before** publishing; it consumes new records with no group offset commits:

```sh
docker compose --env-file .env -f deploy/local/compose.yaml exec -T kafka \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic orders.events.v1 --max-messages 1 \
  --consumer-property enable.auto.commit=false \
  --property print.key=true --property print.partition=true --property print.offset=true
```

In terminal B, publish the committed contract fixture with its exact order key:

```sh
python3 - <<'PY' | docker compose --env-file .env -f deploy/local/compose.yaml exec -T kafka \
  /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server kafka:9092 --topic orders.events.v1 \
  --property parse.key=true --property 'key.separator=|' \
  --producer-property acks=all
import json
from pathlib import Path
event = json.loads(Path('tests/fixtures/order-created.v1.json').read_text())
print(event['order_id'] + '|' + json.dumps(event, separators=(',', ':')))
PY
```

Expected output includes that fixture's order ID, partition/offset and JSON
payload. The static fixture is an old timestamped contract example, not latency
evidence or a measured workload. `make smoke` instead stamps actual publish times,
uses fresh run/event IDs and explicitly verifies all three partitions. Manually
publishing test records adds retained broker records; later experiment groups must
start from captured offsets and isolate run IDs as specified in M0.

## Inspect and stop

```sh
docker compose --env-file .env -f deploy/local/compose.yaml ps
docker compose --env-file .env -f deploy/local/compose.yaml exec -T kafka \
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 \
  --describe --topic orders.events.v1
docker compose --env-file .env -f deploy/local/compose.yaml exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
make logs
make down
```

`make down` keeps data. For an intentional clean experiment reset, this command
**deletes this project's Kafka and PostgreSQL volumes**:

```sh
docker compose --env-file .env -f deploy/local/compose.yaml down --volumes
```

Bootstrap again after a reset. No reset is part of normal startup or tests.
Integration tests create and drop a uniquely named PostgreSQL schema, leaving
business tables intact; their smoke records remain in Kafka. Default tests do
not need containers. Run `make integration` only after `make bootstrap`.

## Troubleshooting

* Docker socket permission denied: start Docker and ensure your account has access
  to its daemon, then rerun `make up`.
* Port already allocated: change matching host port/connection variables in `.env`.
* Missing `.env`: copy the example; Compose refuses an absent password and the CLI
  gives a configuration error. Do not commit credentials.
* PostgreSQL authentication fails after changing `.env`: retained databases keep
  their original credentials. Update them deliberately or reset disposable data.
* Kafka coordinator initially loading: the idempotent producer retries until its
  15s delivery timeout; do not count a failed acknowledgement as success.
* Topic drift or migration checksum mismatch: inspect the retained data/revision.
  Do not hide a mismatch by modifying partitions or editing applied SQL.
* CLI driver errors show only exception type so credentials cannot leak. Inspect
  local service logs for the cause; never paste `.env` or full DSNs into reports.

Reference configuration: [Apache Kafka Docker guide](https://kafka.apache.org/41/getting-started/docker/)
and [official PostgreSQL image](https://hub.docker.com/_/postgres).
