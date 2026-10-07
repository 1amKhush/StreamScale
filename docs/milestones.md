# Local milestone tracker

Owner: coding agent for implementation; user for project acceptance and supplied
external tracker. Estimates below are qualitative scope estimates, not time spent,
velocity or fabricated sprint points. Timestamps record observed transitions in UTC.

| Milestone | Scope estimate | Dependencies | Acceptance | State |
| --- | --- | --- | --- | --- |
| M0 | Small: contract + fixtures + SQL plan | Supplied coding brief | Executable schema/ID fixtures and recorded decisions | Complete locally; started 2026-10-06T19:54:50Z, validated 2026-10-06T19:59:51Z |
| M1 | Medium: packaging + stack + bootstrap + smoke | Coherent M0 | Locked install; healthy Kafka/PostgreSQL; exactly three input partitions; publish/consume + DB smoke | Complete locally; started 2026-10-06T19:59:51Z, validated 2026-10-06T20:06:17Z |
| M2 | Not estimated | Accepted M1 | Seeded logical sequence, faults, IDs, key routing | Out of current scope |

Evidence, defects, review and actual completion timestamps are recorded in
`docs/m0-m1-evidence.md`. Referenced TeX documents and external tracker were not
available locally. No claims are made about reviewing or updating those artifacts.
