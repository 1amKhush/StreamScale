# Reproduce the pipeline and measured comparison

Use a Linux host with Python 3.12, uv, Bash, make and Docker Engine/Compose.
The validated local host and dependency versions are recorded in the generated
`docs/results/current/provenance.json`; match them when comparing results.
First-time tool, image and Python package downloads need network access.

From a fresh checkout:

```sh
./streamscale.sh all --no-ui
./streamscale.sh ui
```

The first command performs M0–M4 local checks, M5 deployment/metrics verification,
M6 controller exercises, M7 pilot/frozen 27-run matrix and M8 report generation.
Expect over an hour for a new measured campaign. The second command opens
Grafana at http://127.0.0.1:13000/d/streamscale and Prometheus at
http://127.0.0.1:19090. The generated Grafana login is in the ignored
`artifacts/k8s/credentials.json`.

Measurements remain in ignored `artifacts/experiments/campaign-*/` directories.
`artifacts/experiments/latest.path` selects the current campaign. Reports retain
the source campaign ID, original Git/image provenance and source file hashes.
The committed report is a snapshot; a new checkout must generate its own raw
campaign before regenerating that report. Generated run IDs and exact timings
will differ. The logical sequence is deterministic for the frozen schedule,
profile and seed, but a new pilot may freeze different calibrated rates.

To repeat the experiment without repeating the earlier checks:

```sh
./streamscale.sh experiments --fresh
./streamscale.sh analysis
```

`--fresh` preserves older SQL rows, broker data and evidence. Without it,
`experiments` resumes the latest unfinished campaign or verifies/reuses the
completed one. Resume retains its original immutable image, protocol and
randomized plan; changed experiment/runtime/chart source requires a fresh
campaign. Interrupted measurements are recovered and excluded before retry.
Do not run another cluster/scaling workflow or manually modify resources while
the experiment lock is held.

To inspect evidence without republishing:

```sh
make experiment-audit
./streamscale.sh analysis
cat docs/results/current/report.md
```

The audit requires all 27 measured cells, verifies raw checksums and CSV values,
recomputes SQL p95/window throughput, and compares actual consumer image IDs
and resources. Analysis refuses incomplete/failed/corrupted evidence. Its
separate project pins Matplotlib 3.11.2 and transitive dependencies in
`experiments/analysis/uv.lock`; the service's root lock stays independent.
Charts use Matplotlib's [static output](https://matplotlib.org/stable/api/_as_gen/matplotlib.figure.Figure.savefig.html)
and [multipage PDF](https://matplotlib.org/stable/gallery/misc/multipage_pdf.html)
facilities, with no live plot server required.

Review `summary.csv` for per-run values and coverage, `aggregate.csv` for
median/range summaries and missing observations, and `frozen-protocol.json`
for rates and schedules. Raw run folders contain SQL timestamps, source
acknowledgements, manifest IDs, one-second lag/replica samples, five-second
resource observations, Prometheus responses and before/after-restart inspections.
Failed attempts are separate and excluded from rankings. The report explains
half-open publishing throughput, drain-inclusive latency, sampled resource
integrals and local-system limitations.

Close the dashboard terminal with Ctrl+C, then stop this project's systems:

```sh
./streamscale.sh stop
./streamscale.sh resume
./streamscale.sh ui
```

Stopping keeps volumes, node containers and artifacts. For separate shutdown
use `stop-local` or `stop-cluster`; `status` lists this project's state. These
commands are scoped to the project and do not prune Docker or delete data.
