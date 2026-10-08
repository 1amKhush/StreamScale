# Workload tool (M2)

`streamscale_workload` is packaged from this directory so the installed CLI can
run it. The consumer never imports it. Generation is an offline one-shot command;
publishing is a separate one-shot command which registers the run before sending.

```sh
uv run --frozen streamscale generate --output artifacts/normal-1 --seed 42 --orders 100
uv run --frozen --env-file .env streamscale publish --run-dir artifacts/normal-1
```

The logical manifest stores relative microsecond business times and publish
schedule, exact amount strings, stable order IDs and fault positions. A fresh
execution gets fresh run/event IDs. Logical hashes exclude those per-run event
IDs; the full manifest hash includes them. Actual `produced_at` is set before
each enqueue attempt and copied into the broker acknowledgement log.

Profiles:

* `normal`: two valid events per order, no injected faults.
* `correctness`: exactly floor(1% of base events) invalid events, floor(2%)
  extra duplicate attempts, and floor(5%) valid events delayed 10 seconds.
  Independent seeded fault selectors are versioned as `v1:{seed}:{fault}`;
  invalid/valid sets are disjoint, while duplicate and late sets may overlap.
  Invalid types cycle through bad JSON, non-UTF-8, invalid amount and wrong key.
* `fixtures`: requires `--orders 2`; seven attempts guarantee normal, duplicate,
  invalid JSON, invalid UTF-8, late arrival and equal-time offset tie cases.

Amounts use integer cents; terminal types use seeded draws (80% paid / 20%
cancelled probability). The generator is pinned to Python 3.12. `generator_version`
and both hashes are preserved in `run.json` and the database snapshot.

The three scenario schedules match the M0 decision record. `--orders` truncates
the schedule, not its rates. Full base workloads are `--orders 6000` for steady,
`16500` for flash-sale, and `48000` for overload-recovery. Delayed injections can
extend publication after the nominal window. Default 100-order runs and `--no-wait`
are development demonstrations, not the planned 27 measured benchmark runs.

Publish one run at a time. Source offsets are captured before/after publishing;
concurrent writes within the captured range cause a failed execution. Real broker
acknowledgements include key/payload bytes, source coordinates and attempt identity.
Partial failure preserves the confirmed subset, marks the artifact failed and
refuses republishing that run. Generate a new directory/run to try again.
