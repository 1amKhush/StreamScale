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
| M7 | Large: pilot, frozen protocol, 27 controlled executions and raw evidence | Validated M6 | Per-run configuration, reconciliation, resources and CSV; correct runs only | In progress; started 2026-10-08T10:51:09Z; owner coding agent |
| M8 | Medium: standalone plots, result report and reproduction guide | Validated M7 | Measured interpretation with explicit limits and presentation evidence | In progress; reporting tools started 2026-10-08T11:49:56Z while M7 collected data; final acceptance requires complete M7 |

Evidence, defects, review and actual completion timestamps are recorded in
`docs/m0-m1-evidence.md`, `docs/m2-m3-evidence.md`, `docs/m4-evidence.md` and
`docs/m5-evidence.md` and `docs/m6-evidence.md`. Referenced TeX documents and external tracker were not
available locally. No claims are made about reviewing or updating those artifacts.
