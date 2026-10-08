# M7 acceptance evidence

The pilot and complete 27-cell matrix passed on 2026-10-08. Campaign
`campaign-20261008T113717-328739` started at 11:37:17 UTC and completed its
measurements at 12:54:17 UTC. The independent complete audit passed at 12:55:51
UTC. M7 implementation began at 10:51:09 UTC; these are observed transitions,
not estimated effort or sprint velocity.

The campaign contains 56 accepted executions: one pilot, one pilot warmup,
27 separate cell warmups and 27 measured runs. Every mode/scenario pair has
three repetitions, with fresh run UUIDs and Kafka groups. The logical manifest
hash matches across all nine executions of each scenario. The matrix order was
shuffled with seed 4242 before measurement.

The unpaced 1,000-event pilot measured 130.497261514 accepted events/second.
Protocol v2 froze steady at 52 and surge at 390 events/second before the first
measured execution. The 60-second scenario schedules and separate 30-second
warmups are documented in [the protocol](experiments.md). These shorter local
windows are explicitly distinct from the original fixed-rate 120-second plan.

All measured runs together produced 256,572 acknowledgements, 249,048 unique
SQL facts, 5,022 intentional duplicate attempts and 2,502 invalid sources with
2,502 uniquely reconciled DLQ sources. Every run passed before and after the
controlled restart: zero missing acknowledged valid IDs, zero unexpected valid
IDs, zero duplicate stored IDs, zero latest-state regressions and zero remaining
lag. The current measured campaign has zero failed/excluded attempts.

The independent audit verified 3,818 raw observation samples across all accepted
executions. The lowest measured publishing-window lag sample coverage was
98.33%. It checked source-file hashes, comparison CSV values, empirical p95 and
half-open throughput against raw SQL timestamps, controller isolation/targets,
fresh groups and unchanged actual consumer image/resource settings. Recovery,
scaling response and resource coverage are explicit CSV fields; missing
observations remain missing rather than being converted to zero.

Every execution used `streamscale:c878a885b5640f3b`, built from committed
`1e3ceee` with a clean working tree. The build image digest is
`sha256:54edb7d6d408c98fb247052819a057d26df7729b976cc63307c8603cec737f44`.
The platform manifest observed in every consumer pod is
`sha256:d4939132d5faa570fae131d109e18f4ef6033a4a6467b3efe81a3745d2aac99b`.
Requests remained 100m CPU / 128Mi and limits 500m CPU / 256Mi per consumer;
fixed stayed at one, autoscaled modes stayed within 1–3. Reporting-only commits
made afterward did not alter this measured image or frozen runtime/protocol.

`experiments/run.py` owns the pilot, freezing, randomized plan, capture,
reconciliation, restart and resume. `experiments/metrics.py` defines measurement
windows and calculations. `src/streamscale/measurements.py` exports committed
SQL facts through the CLI. `experiments/evidence.py` independently audits the
archived evidence, including actual pod controls. The generator supports an
explicit validated schedule and stores experiment configuration in run metadata
and PostgreSQL. The launcher/Make targets include measurement and audit.

Actual commands and records:

```sh
./streamscale.sh experiments --fresh
python3 -m experiments.evidence
```

The experiment engine completed all 27 cells and restored a fresh fixed demo.
That demo passed SQL, metrics and Grafana verification with 396 facts, 200 states,
four DLQ sources and zero lag: `run-20261008125419-b4bfcab5`, UUID
`5818cece-9bbc-43da-a1c5-75ade07c18b5`. The running older shell subsequently
reported `er_action: command not found` because the launcher file had been
updated during its execution. Its exit 127 is recorded, rather than reported
as a successful whole-launcher run. The current launcher must be validated
without editing its file during execution; M8 records that final validation.

Earlier startup validation caught a mismatched protocol-version field before
publication and an exporter port-forward opened before its server listened.
The field was aligned and a regression check added. The sampler now waits for
the exporter's successful Kubernetes startup probe. The unusable 900-event
warmup was excluded and reconciled before fresh publication; its data and logs
remain retained. Neither attempt entered the final measured matrix.

`artifacts/m7-full-check.log` records the initial full flow (135 unit tests,
28 live integration tests, Ruff and M5/M6 checks passed before the protocol
startup failure). `artifacts/m7-campaign.log` records the excluded warmup.
`artifacts/m7-campaign-ready.log` records the completed campaign and old-shell
exit. `artifacts/m7-final-audit.json` records the complete independent audit.
Raw manifests, acknowledgements, SQL timestamps, inspections, resource/replica
samples, exporter text, Prometheus range responses and checksums are under
`artifacts/experiments/campaign-20261008T113717-328739/` and remain ignored.

The compact committed snapshot is [summary.csv](results/current/summary.csv),
[frozen-protocol.json](results/current/frozen-protocol.json) and
[audit.json](results/current/audit.json). See M8 for plots, interpretation and
complete launcher acceptance. Review used local code inspection, regression
checks and live evidence; the optional CodeRabbit CLI was signed out.
