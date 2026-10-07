# StreamScale

A Python Kafka-to-PostgreSQL flash-sale analytics experiment comparing fixed,
CPU-HPA and lag-KEDA consumer scaling. **M0 and M1 are implemented:** the
executable contract and a reproducible local foundation. The workload generator,
continuously running consumer and scaling experiments begin at M2 and later.

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker Engine with
Docker Compose, and `make`. Run these commands from the repository root:

```sh
cp .env.example .env
uv sync --frozen
make up
make bootstrap
make smoke
make test
make integration
make check
```

`make up` waits for healthy Kafka and PostgreSQL. `make bootstrap` explicitly
creates/verifies the two topics and applies checksum-protected SQL migrations;
it is safe to repeat with retained volumes. `make smoke` publishes fresh valid
events with `order_id` keys, waits for acknowledgements, consumes this run's
records from all three partitions, and checks a committed PostgreSQL decimal
round trip. It writes no business facts and commits no source offsets.

Kafka is available at `127.0.0.1:19092`; PostgreSQL at `127.0.0.1:15432`.
Local credentials are in the ignored `.env`. Change matching connection settings
there when overriding ports or credentials. Both ports bind to loopback only.
`make down` stops the stack and preserves its volumes.

| Location | Responsibility |
| --- | --- |
| [Contract decisions](docs/contract-v1.md) | Event/run identity, late-event rule, faults, measurement definitions |
| `schemas/` | Versioned order event and DLQ JSON Schemas |
| `src/streamscale/` | Contract helpers, local bootstrap, migrations and smoke CLI |
| `db/migrations/` | Three business tables, constraints and immutable history |
| `deploy/local/` | Digest-pinned single-broker KRaft Kafka and PostgreSQL |
| `tests/` | Contract fixtures, bootstrap guards and live integration tests |
| [Local operations](docs/local-development.md) | Manual publish/consume, inspection and troubleshooting |
| [Milestone evidence](docs/m0-m1-evidence.md) | Actual commands, results, review and remaining scope |

The event contract uses exact decimal **strings**, fresh run IDs and globally
distinct run-scoped event IDs. PostgreSQL is committed before source offsets in
the future consumer; invalid records require acknowledged DLQ delivery. Processing
is at-least-once. No throughput improvement or exactly-once result is claimed.
