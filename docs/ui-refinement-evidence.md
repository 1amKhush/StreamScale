# Console usability refinement — 2026-10-12

The original console mixed monitoring, configuration and inspection in one long
page. Saved presets expanded the sale form indefinitely, and eight charts competed
with the live map. The refined console separates these tasks while keeping live
metrics, warnings and fault recovery available.

## Delivered

- Persistent navigation for Overview, Sale, Scaling, Failure lab, Sessions and Guide.
- Four key metrics, a wider pipeline map and two primary charts on Overview.
  Additional metrics, activity, source diagnostics and view preferences expand
  when needed.
- Map and keyboard-accessible List views share an inspector drawer. Phones default
  to List. The drawer supports component switching, order/event lookup, Escape,
  focus containment and return to the opener. It stops polling when closed.
- Four built-in sale cards, a compact saved-preset selector, expandable customization
  and capacity estimates. Drafts survive navigation. Start, Pause, Resume and
  Stop & drain use consistent labels in both lenses.
- Scaling gets a dedicated page with mode cards, manual counts and overrides;
  controller tuning and history expand separately.
- Failure lab shows one selected experiment with its parameters. Active faults,
  recovery and verified outcomes remain available. Failed recovery operations
  display their error.
- Responsive light/dark styles, visible keyboard focus, reduced-motion support,
  a skip link and a bounded history of fault outcomes.
- README, launcher help and run/console guides describe the new workflow.

The Kafka/PostgreSQL processing implementation, deployment behavior, dependency
locks and measured M7/M8 results were not changed. Existing sale, scaling, fault,
replay and comparison features remain available.

## Validation

| Check | Result |
| --- | --- |
| Production build (`npm run build`, including TypeScript checks) | Passed |
| Frontend unit tests (`npm test`) | 19 passed |
| Complete Chrome suite against the running kind pipeline (`npm run e2e`) | 12 passed, 4.8 minutes |
| Final connection/usability rerun after inspector polling and recovery-error fixes | 6 passed, 9.5 seconds |
| Launcher syntax, help and `git diff --check` | Passed |

The complete browser run exercised real flash-sale traffic, bounded particles,
SQL/DLQ inspection, chart export, pause/resume/rate changes, abort and reconciliation,
manual rescue from one to three consumers, crash-after-DB-commit replay, a bounded
PostgreSQL outage and recovery, session replay, comparison views, and the measured
matrix. The full journey reconciled with zero missing acknowledged events, zero
duplicate stored IDs and zero state regressions.

Three new usability tests cover progressive disclosure, keyboard inspection and
focus return, draft preservation across task navigation, one experiment at a time,
and all six pages at 390px width in dark/reduced-motion mode without horizontal
page overflow. Existing journeys now navigate to the relevant task pages; their
pipeline assertions are retained.

Final live inspection: publisher finished, scaling fixed, backlog zero, no active
faults. User services remain running. The baseline/full M0–M15 launcher was not
rerun for this frontend refinement; its behavior did not change. The existing
`console-check` and `all` workflows discover the added Playwright tests automatically.
CodeRabbit was signed out; local code review and the React best-practices checklist
were used alongside the automated checks.

Screenshots are retained locally:

- `artifacts/console/ui-before.png`
- `artifacts/console/e2e/usability-overview.png`
- `artifacts/console/e2e/usability-sale.png`
- `artifacts/console/e2e/usability-mobile-dark.png`
- `artifacts/console/e2e/journey-1-overload.png` through `journey-4-reconciled.png`

## Open and check

Refresh the existing console browser tab to load the rebuilt assets. To start the
console when it is not running, use the maintained launcher from the repo root:

```sh
./streamscale.sh console
```

For frontend checks while a console server is already running:

```sh
cd console/web
npm run build
npm test
npm run e2e
```

The browser suite controls real sales and injects bounded faults. The complete
console acceptance remains `./streamscale.sh console-check` from the repo root.
