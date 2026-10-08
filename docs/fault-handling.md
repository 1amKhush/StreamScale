# M4 fault handling

Start the local dependencies with `make up bootstrap`, then run `make faults`.
The live tests use temporary PostgreSQL schemas and fresh run IDs/groups. They
leave existing business rows and containers intact. Kafka test records remain
subject to the configured retention policy.

| Fault | Handling and evidence |
| --- | --- |
| PostgreSQL unavailable | Three write attempts; 0.2 then 0.4 seconds backoff. Exhaustion stops the consumer with the record uncommitted and no DLQ entry. Restarting the same group after recovery drains the run. |
| Lost DB session | Reconnect and retry the complete transaction. A committed event is detected by its identity on replay. |
| DLQ broker unavailable | Fail the record without committing its source offset. Restart after recovery. |
| Crash after DB commit | Exit 75; restart the same group. The fact remains unique and the projection stays correct. |
| Crash after DLQ acknowledgement | Exit 76; replay can add another envelope for the same source coordinate. Reconciliation counts copies separately from unique invalid sources. |
| Join/leave rebalance | Clear ownership on revoke/loss, resume the new assignment at committed offsets, and reject a stale batch before its source commit. A live two-member test verifies disjoint assignments and reassignment to the surviving member. |
| Late or duplicate event | Preserve late facts; update state only for a greater `(event_time, kafka_offset)`. Duplicate IDs must match logical fields and partition. |

For a manual DLQ crash demonstration, use a fresh directory:

```sh
uv run --env-file .env streamscale generate --output artifacts/dlq-crash --orders 2 --profile fixtures
uv run --env-file .env streamscale publish --run-dir artifacts/dlq-crash --no-wait
uv run --env-file .env streamscale consume --run-dir artifacts/dlq-crash --crash-after-dlq-ack 1
# Exit 76 is deliberate. Restart with the same run directory and group:
uv run --env-file .env streamscale consume --run-dir artifacts/dlq-crash
uv run --env-file .env streamscale inspect --run-dir artifacts/dlq-crash
```

`consume --audit-log artifacts/member.jsonl` records assignment/revocation
transitions. `--stay-running` keeps polling after drain so a service remains a
group member; SIGTERM stops between records and closes the client. Graceful
shutdown does not commit buffered records. The DB and Kafka are separate systems:
processing is at least once, SQL identity is idempotent, and DLQ copies can repeat.
