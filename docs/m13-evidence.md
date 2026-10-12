# M13 evidence: failure lab and recovery

Started 2026-10-11T22:54Z, after M12 (`9e3a39f`). Acceptance comes from
`make console-check`; the log is `artifacts/console/m13-console-check.log`.

## Scope delivered

- **Fault supervisor** in the agent (`faults.py`) with ten bounded faults: restart
  a consumer; crash after a DB commit (exit 75); crash after a DLQ acknowledgement
  (exit 76); memory exhaustion to OOMKilled; all consumers to zero; per-record
  processing delay; in-place consumer CPU restriction; in-place PostgreSQL CPU
  restriction; temporary PostgreSQL outage; temporary Kafka outage. Parameters
  are validated (durations 5–300 s, outages 5–120 s, delay 5–2,000 ms, CPU
  20–400m) and only one infrastructure outage may run at a time.
- **Supervised recovery**: the agent persists active faults and their deadlines on
  the runs volume and restores them on expiry, on request, through *Recover
  pipeline*, or after its own restart. CPU restrictions are reapplied to
  replacement pods while active and restored to the chart's 500m/100m afterwards.
- **Observed outcomes**: crash faults record the consumer's own claim (pod, event,
  partition, offset, exit code); a crash after a DB commit is marked verified only
  once the replacement has committed past that offset and SQL holds the event
  exactly once; a DLQ crash reports the copies seen for that source; the OOM
  fault is verified from the pod's termination reason.
- **Consumer hooks** inside the console consumer process only, at the exact
  boundaries (after the PostgreSQL commit, after the DLQ acknowledgement); the
  measured image and source are unchanged. One-shot faults are claimed atomically
  on the shared volume so exactly one consumer acts.
- **Failure lab tab** with store-lens stories and exact technical scope for each
  fault, parameters, active faults with countdown and events, *Recover now*,
  *Recover pipeline* in the top bar, and recent outcomes with verification.
- Faults are recorded on the session, which keeps it out of comparisons.

## Defects found and fixed during acceptance

1. Live and browser tests looked up crash outcomes by fault kind and could read an
   older, already verified crash. Outcomes are now tracked by the injected fault's
   ID (outcome cards carry `data-fault-id`).
2. The fault tests used the 3,182-event steady preset at raised rates, so a sale
   could end before a fault was observed. They now use a ten-minute schedule.
3. Server shutdown dropped queued operations silently (a queued *Switch to fixed*
   left the floor in manual mode, and the browser test read a stale "succeeded").
   Unfinished operations are now journalled as `abandoned`; each live session
   starts from a baseline floor (faults recovered, fixed mode); the browser test
   waits for the selected mode itself.

## Acceptance

Final run: `make console-check` exit 0, 2026-10-12T02:11:11Z–02:29:10Z, console
image `streamscale-console:e2094cd3fa7010d6`: 128 Python unit tests (adds 15
fault-supervisor tests), 19 web unit tests, 36 live-cluster tests in 902 s and 7
Chrome tests.

Fault sessions from this run (all reconciled against the source log):

| Fault | Observed outcome | Acknowledged | Facts | Missing | Duplicates stored | State regressions | Invalid sources → DLQ copies |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Crash after DB commit | exit 75 after storing event `9ada4547` (1/258230); replayed; stored once (verified) | 3,467 | 3,360 | 0 | 0 | 0 | 39 → 39 |
| Crash after DLQ acknowledgement | exit 76 after INVALID_JSON from 1/259350 was acknowledged; replayed | 5,004 | 4,857 | 0 | 0 | 0 | 54 → **55** |
| PostgreSQL outage, 20 s | committed offsets frozen while records kept arriving; recovered on time | 3,177 | 3,084 | 0 | 0 | 0 | 35 → 35 |
| Kafka outage, 20 s | publisher buffered; acknowledgements resumed; zero delivery failures | 539 | 524 | 0 | 0 | 0 | 8 → 8 |
| Memory exhaustion | OOMKilled (exit 137), restarted (verified) | 1,109 | 1,073 | 0 | 0 | 0 | 15 → 15 |
| Consumer CPU 500m → 100m in place | limit applied without restart, restored afterwards | 2,820 | 2,739 | 0 | 0 | 0 | 30 → 30 |
| Slow processing + DB CPU + consumers to zero, then recover-all | all restored: fixed mode, one member, PostgreSQL limit 500m | 818 | 796 | 0 | 0 | 0 | 11 → 11 |
| Browser: crash after DB commit, then PostgreSQL outage recovered from the top bar | crash verified; outage cleared by *Recover pipeline* | 948 | 920 | 0 | 0 | 0 | 12 → 12 |

The 55 DLQ copies for 54 sources is the documented at-least-once limit in action:
the consumer died after the DLQ acknowledged an envelope and before its offset
commit, so the replacement filed that source again. Reconciliation counts unique
sources and reports the copies separately.

Acceptance criteria:

- **Actual failure reasons appear.** Exit codes 75 and 76 from the crash hooks and
  OOMKilled (137) from the kernel were read from Kubernetes and shown on the floor
  and in the lab; outages raised *PostgreSQL unavailable* and consumer *retrying*
  signals.
- **Pending offsets stay uncommitted during a database failure.** During the
  PostgreSQL outage the three committed offsets were identical six seconds apart
  while acknowledgements increased.
- **Crash after DB commit replays without a second fact.** Each crash was verified
  only after the replacement committed past the crash offset and SQL counted the
  event once; the browser test followed its own fault by ID.
- **Recovery reconciles the acknowledged subset.** Every fault session above
  passed log-based reconciliation with zero missing acknowledged IDs, zero
  duplicate stored IDs and zero state regressions.
- **Unrelated services remain untouched.** Grafana, Prometheus and the measured
  exporter recorded no restarts across the fault tests (asserted); every agent
  write is limited by its Role to console resources and the Kafka/PostgreSQL
  StatefulSet scale.

Screenshots: `artifacts/console/e2e/faults-crash-replay.png`, `faults-postgres-outage.png`.
