# Local milestone tracker

Owner: coding agent for implementation; user for project acceptance and supplied
external tracker. Estimates below are qualitative scope estimates, not time spent,
velocity or fabricated sprint points. Timestamps record observed transitions in UTC.

| Milestone | Scope estimate | Dependencies | Acceptance | State |
| --- | --- | --- | --- | --- |
| M0 | Small: contract + fixtures + SQL plan | Supplied coding brief | Executable schema/ID fixtures and recorded decisions | Complete locally; started 2026-10-06T19:54:50Z, validated 2026-10-06T19:59:51Z |
| M1 | Medium: packaging + stack + bootstrap + smoke | Coherent M0 | Locked install; healthy Kafka/PostgreSQL; exactly three input partitions; publish/consume + DB smoke | Complete locally; started 2026-10-06T19:59:51Z, validated 2026-10-06T20:06:17Z |
| M2 | Medium: deterministic manifest + publisher | Validated M1 | Seeded logical sequence, fixtures, acknowledged faults, IDs and key routing | Complete locally; started 2026-10-07T12:53:17Z, validated 2026-10-07T13:03:43Z |
| M3 | Medium: storage + sequential consumer + replay evidence | Validated M2 | Transactional history/state, explicit Kafka commits, crash-after-DB replay | Complete locally; started 2026-10-07T13:03:43Z, final checks 2026-10-07T13:20:28Z |
| M4 | Medium: failure recovery + live campaign | Validated M3 | Deliberate outage, DLQ failure and restart/rebalance campaign | Complete locally; started 2026-10-07T21:08:28Z, validated 2026-10-08T00:26:31Z |
| M5 | Large: container + kind/Helm + monitoring | Validated M4 | One cluster consumer; SQL reconciliation and inspectable labelled metrics | Complete locally; started 2026-10-08T00:26:31Z, validated 2026-10-08T00:52:16Z |
| M6 | Medium: controller dependencies, isolated chart modes, live bounds checks | Validated M5 | Exercise fixed/CPU-HPA/lag-KEDA independently; verify 1–3 bounds | Complete locally; started 2026-10-08T10:17:16Z, validated 2026-10-08T10:46:37Z |
| M7 | Large: pilot, frozen protocol, 27 controlled executions and raw evidence | Validated M6 | Per-run configuration, reconciliation, resources and CSV; correct runs only | Complete locally; started 2026-10-08T10:51:09Z, campaign completed 2026-10-08T12:54:17Z, independent audit validated 2026-10-08T12:55:51Z |
| M8 | Medium: standalone plots, result report and reproduction guide | Validated M7 | Measured interpretation with explicit limits and presentation evidence | Complete locally; tools started 2026-10-08T11:49:56Z while M7 collected data, full M0–M8 launcher validated 2026-10-08T13:13:40Z |

| M9 | Medium: console API, agent, telemetry stream, browser shell | Validated M0–M8 | Real cluster data; explicit stale/disconnected states; cross-project targets rejected | Complete locally; started 2026-10-11T20:44:10Z, validated 2026-10-11T21:07:17Z |
| M10 | Medium: live flow map, charts, inspector | Validated M9 | A real sale changes topology/backlog/outcomes; usable under burst and reduced motion | Complete locally; started 2026-10-11T21:08Z, validated 2026-10-11T22:19:00Z |
| M11 | Large: interactive publisher and session controls | Validated M10 | Pause/resume/rate/stop with acknowledged-event accounting | Complete locally; started 2026-10-11T22:20Z, validated 2026-10-11T22:40Z |
| M12 | Large: scaling modes and manual override | Validated M11 | Manual rescue and both autoscalers without competing controllers | Complete locally; started 2026-10-11T22:41Z, validated 2026-10-11T22:53:30Z |
| M13 | Large: failure lab and recovery | Validated M12 | Crash/replay and outages recover with correct facts and state | Complete locally; started 2026-10-11T22:54Z, validated 2026-10-12T02:29:10Z |
| M14 | Medium: replay, comparison, exports, guided demos | Validated M10–M13 | Playback matches evidence; unsuitable runs excluded | Complete locally; started 2026-10-12T02:32Z, validated 2026-10-12T03:06:07Z |
| M15 | Medium: browser-to-pipeline acceptance | Validated M9–M14 | Sale → overload → scale → fault → recover → reconcile | Complete locally; started 2026-10-12T03:07Z, full launcher validated 2026-10-12T07:32:18Z |

Console milestones follow [the console plan](interactive-console-plan.md); evidence is in `docs/m9-evidence.md`, `docs/m10-evidence.md`, `docs/m11-evidence.md`, `docs/m12-evidence.md`, `docs/m13-evidence.md`, `docs/m14-evidence.md` and `docs/m15-evidence.md`.

The [console usability refinement](ui-refinement-evidence.md) was validated on 2026-10-12: focused task pages, progressive controls, mobile inspection, and 12 passing browser checks against the real pipeline.

Evidence, defects, review and actual completion timestamps are recorded in
`docs/m0-m1-evidence.md`, `docs/m2-m3-evidence.md`, `docs/m4-evidence.md` and
`docs/m5-evidence.md`, `docs/m6-evidence.md`, `docs/m7-evidence.md` and
`docs/m8-evidence.md`. Referenced TeX documents and external tracker were not
available locally. No claims are made about reviewing or updating those artifacts.

| M16 | Medium: observed-history features, quantile HGBT models and artifacts | M0–M15 | Leakage/split/integrity tests; live trace preparation | Supplied 2026-11-12 evidence records 12 training and 4 validation runs, disjoint IDs, model metadata and 288 diagnostic slices; metadata/runtime hashes checked locally |
| M17 | Large: agent predictive controller and exclusive ownership | M16, M12 | Replica bounds, skew, stale data, override/release | Trained mode recorded in 9 held-out sessions, all correctness passing; source report declares full live acceptance, whose raw logs are not in the imported bundle |
| M18 | Large: CPU-only Bayesian preparation, checkpoints and reuse | M16–M17 | Twelve live candidate policies; resumable accepted artifacts | Recorded preparation complete: 12 policies / 24 tuning captures; none feasible for p95 ≤ 2s, conservative default retained; imported summaries do not install the model |
| M19 | Medium: single ML Predictive choice, chart, exports and replay | M17–M18 | Web tests/build and browser pilot | Supplied report records 193 console, 29 web, 45 live and 20 Playwright checks passing; detailed live/browser logs are report-only in this bundle |
| M20 | Large: held-out campaign and complete launcher | M16–M19 | 36 held-out runs, reconciliation, full launcher and reuse | 36/36 unique complete correctness-passing summary rows audited on 2026-11-12; 15 meet latency target. Full-launcher/reuse logs were not supplied, so those gates are not independently established by this import |

Predictive evidence and limitations: [ML Predictive](predictive.md) and
[implementation acceptance](predictive-evidence.md). These states distinguish
implemented code, directly auditable run summaries and source-report declarations.
The [imported-run digest](results/capacity-12/sim-run-2026-11-12/README.md) and
[model analysis](predictive-model-analysis.md) supersede earlier pending notices
for preparation and the held-out matrix. A final project report is still to follow.

## Capacity expansion

| Milestone | Scope estimate | Dependencies | Acceptance | State |
| --- | --- | --- | --- | --- |
| M21 | Large: one twelve-partition configuration and 1–12 scaling | M0–M20 | SQL/topic upgrade, all replica counts, predictive calibration, full launcher and browser acceptance | Supplied complete-run report declares acceptance on 2026-11-12; metadata confirms 12 partitions / 1–12 range, and 36 held-out summaries are audited. Its separate 1.16s capacity check and live/browser completion lack individual raw records in this bundle; historical local acceptance/cleanup evidence is retained |

M21 retains the historical three-partition milestones and measured reports above.
Current configuration and acceptance evidence: [twelve-partition capacity](capacity-evidence.md).
