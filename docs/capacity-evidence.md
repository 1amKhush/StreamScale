# Twelve-partition capacity acceptance (M21)

Requested configuration: twelve input and DLQ partitions, initial one consumer,
1–12 replicas for CPU-HPA, lag-KEDA and ML Predictive. Fixed remains one; manual
zero retains the existing pause-processing behavior. No extra runtime mode was added.

Status on 2026-11-09: implementation complete enough for validation; **live
acceptance is still running**. This document does not assert complete acceptance.


## Why the range changed from three to twelve

Three replicas were sufficient to prove correctness and basic autoscaler ownership,
but offered only two scale-up steps. That narrow range made it difficult to study
gradual scale-up, stabilization, manual rescue, predictive decisions and the
tradeoff between latency and replica-seconds. Twelve gives those behaviors room
to differ while retaining one configuration across all modes.

Kafka assigns a partition to one member of a consumer group at a time. Raising
only the replica limit would leave extra consumers idle, so both input and DLQ
topics now have twelve partitions. Twelve also divides evenly at 1, 2, 3, 4, 6
and 12 consumers; intermediate counts exercise uneven assignments and skew.

This is an experimental-capacity decision, not a promise of twelvefold throughput.
The local node and shared PostgreSQL database still impose limits. Capacity is
measured independently at every count, and overload remains explicitly visible.
The historical three-partition results cannot establish improvements for the new
configuration; those require the separate held-out twelve-partition comparison.

| Setting | Historical system | Maintained configuration |
| --- | --- | --- |
| Input and DLQ partitions | 3 each | 12 each |
| Autoscaler range | 1–3 | 1–12 |
| Initial / fixed consumers | 1 | 1 |
| Useful parallelism ceiling | 3 consumers | 12 consumers |
| Predictive capacity calibration | Counts 1–3 | Every count 1–12 |

These are the old and new configurations, not selectable capacity profiles.

## Changes under test

- Topic/bootstrap, CRC32 key routing, source-offset completion, lag export and
  inspection cover all twelve partitions. An explicit bootstrap upgrade can
  expand only legacy three-partition topics, retaining records and offsets.
  Active topic consumers and changing watermarks block expansion. Publishers
  must be closed; the sampling guard is not a distributed publisher lock.
- SQL migration `0002_twelve_partitions.sql` widens fact/state coordinate checks
  to 0–11. It changes no stored facts, original migration checksums or event IDs.
  The DLQ envelope accepts the expanded source range; event payloads stay v1.
- Chart/API/manual/override/autoscaler limits agree on twelve. Persisted legacy
  controller bounds upgrade under the exclusive actuator lock. Later user bounds
  and overrides remain available. Incomplete transitions remain explicit.
- Predictive control evaluates twelve targets using separately calibrated
  aggregate capacities. It uses actual stable assignments, or a conservative
  balanced-range bound after rebalance, including skew and startup delay. The computation is
  polynomial in partition and target count rather than exponential enumeration.
- Calibration runs every size 1–12 with saturated 20-second publishing intervals
  and full drain. It does not extrapolate linear capacity. Expanded workloads
  exercise more of the range, within the existing 2,000 events/s publisher limit.
- Artifacts include partition count, replica bounds and feature version 2; old
  models are incompatible. Fingerprints separate new traces and comparisons.
- Console controls, charts, lane ownership, palettes, map layout, guided demos and
  searchable Docs cover all twelve. Controls wrap on narrow screens. Historical
  three-lane replay data render with their recorded partition count.
- `capacity-check` joins the maintained `all` workflow. A completed original M7
  campaign can be audited as historical evidence despite code drift; incomplete
  incompatible campaigns remain blocked. The historical report is preserved.

## Verification log

The first launcher pass exposed the original SQL 0–2 bounds; a versioned widening
migration fixed them. Its interrupted fixture was replayed and reconciled with
four unique facts, no missing IDs, no duplicate stored IDs, no state regression,
and zero remaining lag across all twelve partitions.

Current measured summaries are in [the capacity report](results/capacity-12/report.md),
with machine-readable acceptance and calibration data. Full workflow acceptance
remains pending; the report separates correctness from the missed latency target.
The initial launcher log is retained at `artifacts/capacity/full-launcher-initial.log`;
the resumed full workflow writes `artifacts/capacity/full-launcher.log`.
Generated evidence is ignored under `artifacts/`; historical milestone evidence
and `docs/results/current` remain available.

CodeRabbit CLI 0.7.0 is installed but signed out. External CodeRabbit review was
not performed; source review, the React checklist and executable checks are used.

The initial full launcher was interrupted at a completed manual-capture boundary
after calibration exposed a capacity-limit selection defect: saturated targets
shared one sentinel score, which favored the largest count even when measured
throughput declined. The controller now retains finite projected delays and
reports saturation separately; a regression test chooses the best measured
capacity under infeasible demand. All twelve calibration captures and four
completed manual training captures remain preserved with checksums and original
source provenance. Manual collection, feature extraction, resources and the
environment are unchanged; no policy-controlled evidence is reused across this
controller correction. The final model/policy and held-out campaign use the
corrected controller fingerprint.

Expanded overload comparison workloads include 60 seconds of observed warm-up,
a 30-second surge and a 90-second recovery tail. Peaks exercise the wider range
without creating a fixed-baseline backlog that cannot drain within the campaign's
bounded timeout. Recovery starts at the end of the surge, not at the final low
phase. These schedule details are offline experiment metadata, never inference
features.

Final backend checks after the controller correction: **194 pipeline/unit tests**,
**149 console unit tests**, **31 live local integration tests** (including real
3-to-12 Kafka expansion with retained records and SQL partitions 3/11 accepted,
12 rejected), Ruff and formatting all pass. The web build and 26 web unit tests
pass. The requested UI subagent completed nine real Chrome checks covering
Docs/usability plus twelve-consumer control and retained three-partition replay.
Measured node heights prevent overlap during rebalances, maps scroll within
their own region, all twelve chart series remain visible, and all thirteen manual
choices (including pause at zero) fit on a phone. Older replay topology remains
unchanged. The current README image is a native browser capture of twelve ready
consumers, each assigned one partition during a real sale.
Raw check output is retained under `artifacts/capacity/`.


## Larger-workload memory correction

The resumed launcher reached repeated-burst training after seven completed
training captures. A 70,920-attempt sale published and drained without failed
acknowledgements, but reconciliation caused the agent to exceed its 768 MiB
memory limit (`OOMKilled`). Its source data and SQL facts were retained. The host
then lost its port forward and left that reconciliation unfinished. This failure
is part of the acceptance record, not a passing campaign result.

Reconciliation now streams source records, acknowledgement logs and server-side
SQL cursors into temporary SQLite proof tables with an 8 MiB page cache. It keeps
exact identifier, state, DLQ and nearest-rank percentile checks without retaining
multiple payload/row copies. Only its own temporary directory is removed; prior
results are published atomically after completion. The agent's disk-backed `/tmp`
allowance is 1 GiB for this transient work; its memory limit stays 768 MiB.
Telemetry backfill is encoded and capped at 32 MiB / 1,200 frames, plus the latest
live sample. Full session recordings on disk are preserved. Forecast observation
fields, sampling cadence and the 60-second inference history are unchanged.

Host reconciliation retries after reconnect and restarts lost jobs. Request
identities prevent an older persisted success from satisfying a new operation.
Regression checks cover large streamed proofs, duplicate/late/tied events, DLQ
copies, missing facts, cleanup after failure, history bounds and lost POST replies
or agent jobs. All **157 console unit tests** and repository lint/format checks
pass; live recovery and the complete launcher remain pending for this correction.
The interrupted launcher log is retained at
`artifacts/capacity/full-launcher-before-memory-fix.log`.


Live recovery after the correction passed for the retained 70,920-event sale:
70,920 unique facts, zero missing IDs, zero duplicate facts and zero state
regressions. The agent stayed on the same pod with zero restarts; sampled peak
cgroup memory was 239,534,080 bytes (228.44 MiB). This is sampled usage, not a
continuous memory high-water measurement. A second live check exactly reproduced
the previous fault-mix reconciler's identifier, state, DLQ and percentile results.
Reviewed JSON evidence is under `docs/results/capacity-12/`.

The focused UI review also found that many short-lived replacements could evict
a still-running consumer from the CPU chart. Current non-deleting Running pods
now get series slots first, with recent historical pods filling remaining slots.
Three regressions cover churn, all twelve current pods and drained replay.
**29 frontend unit tests** and the production build/type check pass; earlier nine
focused browser checks passed before this final pure-selector correction. The
complete browser suite remains a required full-launcher gate.

The corrected agent has a new preparation fingerprint. Earlier traces remain
retained, but the next complete preparation collects calibration and training
under this final collector version rather than treating the previous model cache
as compatible. No trained model has yet been promoted for the new configuration.


## Formal comparison completion gate

Review reproduced a separate eligibility defect: a correctness-passing interrupted
sale could be marked performance-eligible. The gate now requires a completed,
unmodified sale, all planned acknowledgements and (for ML) recorded forecasts.
It checks newly captured runs and cached captures; held-out evaluation cannot
write completion evidence for an ineligible row. Pipeline correctness and the
latency objective remain separate outcomes.

The coordinator paused at a completed capture after twelve fresh calibrations
and seven training runs. All nineteen manual captures passed the stricter gate,
configuration equality and twelve-partition observation checks. Only post-capture
acceptance/reporting checks changed; collection, feature extraction, controller,
workload schedules, dependencies and environment are unchanged. Their original
files remain retained, and checksummed copies with provenance seed the new
preparation fingerprint. No model, tuned policy or policy-controlled capture is
reused across this correction. The interrupted log is retained at
`artifacts/capacity/full-launcher-before-completion-gate.log`.

All **166 console unit tests**, the focused eligibility regressions and repository
lint/format checks pass. Fresh calibration completed every size from one through
twelve; the agent remained at zero restarts during the sequence, with a reported
496 MiB working set under twelve-consumer telemetry. The reviewed current curve
and source run IDs are in `docs/results/capacity-12/final-calibration.*`; the
initial curve remains available separately. Training/tuning and complete workflow
acceptance remain pending.


## Publisher finalization memory correction

The next preparation completed all twelve training captures and two validation
captures. Repeated-burst validation acknowledged all 93,060 planned attempts,
but the agent was OOM-killed at 2026-11-09T18:57:28Z while finalizing publication.
Its recovered run remained explicitly interrupted. Exact reconciliation passed
with 93,060 facts, no missing or duplicate identifiers and no state regressions;
the strict experiment gate correctly excluded it. Its p95 was 98.39849 seconds,
which misses the two-second objective. The retained session is
`session-20261109185425-3fc60e`; it is not experimental performance evidence.

Publisher finalization and restart recovery now validate/count acknowledgement
lines and hash their exact bytes incrementally. Partial-tail truncation scans
backwards in 64 KiB blocks. Recovery uses the durable planned count without
loading the manifest, and finished publishers release their in-memory manifests.
Read-only event/order inspection also streams the acknowledgement log. These
changes preserve the event sequence, acknowledgement bytes and interrupted-run
classification. The agent memory limit remains 768 MiB.

Eight regressions cover a 40 MiB acknowledgement log with less than 3 MiB of
traced Python peak allocation, exact count/hash, long partial tails, incomplete
recovery, released-manifest status, corrupt complete records and streaming
inspection. All **174 console unit tests** pass. Fresh large-sale/live acceptance
and the complete launcher remain pending. The previous failure log is retained
at `artifacts/capacity/full-launcher-before-publisher-memory-fix.log`.

After qualification, the user requested scoped shutdown and disposable build
cleanup. Run evidence and data volumes must remain available; no global Docker
stop/prune or unrelated resource cleanup is part of that work.


The fresh 93,060-event retry (`session-20261109212841-3cb849`) completed
publication and exact reconciliation with zero missing IDs, duplicate facts or
state regressions. The same agent pod remained at zero restarts and zero OOM
events. Its kernel-recorded cgroup peak since startup, read after reconciliation
when the next validation had begun, was 419.66 MiB under the 768 MiB limit. This
is a pod-wide high-water reading, not an isolated allocation profile of the sale.
Exact p95 was **286.387491 seconds**: correctness passed and the latency objective
failed. This manual validation workload is not a held-out comparison. See
[publisher recovery evidence](results/capacity-12/publisher-memory-recovery.json).

All twelve training captures and the first three validation captures are now
complete. Twenty-six earlier completed manual captures were revalidated and copied
byte-for-byte with checksums into the publisher-correction fingerprint; their
active collection, features, controller and environment were unchanged. No
interrupted run or policy-controlled evidence was reused. Compatibility provenance
is retained at `artifacts/capacity/publisher-memory-compatible-captures.json`.
Tuning, full browser/live acceptance, held-out evaluation and launcher reuse
remain pending.

Six recorded private StreamScale dependency build caches were removed after
large-sale recovery, freeing about 2.79 GiB. This deletion targeted exact cache
IDs, with matching build commands and recorded project build history. It removed
no images, containers, volumes or run evidence. Services remain running for the
remaining checks and will be stopped at the end as requested. The cleanup audit
is retained at `artifacts/capacity/cache-cleanup.json`.


## Twelve-key model metadata round trip

All twelve training and four validation captures completed and the forecaster
trained. The first live tuning workload exposed an artifact-publication defect:
integer capacity keys sorted numerically before hashing, but JSON readers saw
string keys and sorted 1, 10, 11, 12, 2, ... . Three-count metadata had hidden
this difference. Model bytes matched their checksum; the metadata identifier
did not. The agent rejected the candidate and held replicas with an explicit
checksum-error fallback. No trained artifact was promoted and no fallback-only
tuning run can pass the formal comparison gate.

Publication now normalizes metadata to its JSON representation before hashing
and rejects non-finite values before replacing any valid artifact pointer.
Installation validates the candidate locally before any cluster write. A real
trained-model round-trip regression includes all twelve integer capacity keys;
additional checks preserve the prior working artifact on invalid metadata and
reject a corrupt candidate before contacting Kubernetes. **195 pipeline/unit**
and **175 console unit tests** pass, along with repository lint/format checks.
Live tuning, browser acceptance, evaluation and full launcher reuse remain pending.
