"""CLI for trusted scenario files and matched baseline/candidate campaigns."""

import argparse
import copy
import json
import random
import signal
from pathlib import Path
from statistics import median

from streamscale.engine.model import validate
from streamscale.engine.runner import EXIT, digest, logical_plan, run, save


def compatibility(config):
    return digest(
        {
            "plan": logical_plan(config),
            "expected": config.get("expected"),
            **{
                key: config.get(key, default)
                for key, default in {
                    "warmup_seconds": 0,
                    "drain_seconds": 30,
                    "poll_seconds": 0.1,
                    "max_lateness_seconds": 0.5,
                    "min_achieved_fraction": 0.95,
                    "ack_timeout_seconds": 30,
                    "thresholds": {},
                }.items()
            },
            # Fault definitions must match; target-specific setup belongs in before_load.
            "fault": config.get("hooks", {}).get("fault", {}),
        }
    )


def summarize(runs):
    eligible = [r for r in runs if r["eligible_for_comparison"]]
    values = [
        r["checks"]["p95_seconds"] for r in eligible if r["checks"]["p95_seconds"] is not None
    ]
    return {
        "runs": len(runs),
        "eligible": len(eligible),
        "median_p95_seconds": median(values) if values else None,
        "statuses": [r["status"] for r in runs],
    }


def compare(baseline, candidate, directory, repetitions=3):
    for config in (baseline, candidate):
        validate(config)
    if compatibility(baseline) != compatibility(candidate):
        raise ValueError("Compared workloads, checking rules, windows and thresholds must match")
    if ("fault" in baseline.get("hooks", {})) != ("fault" in candidate.get("hooks", {})):
        raise ValueError("Both targets must declare the matched fault")
    if not 1 <= repetitions <= 100:
        raise ValueError("repetitions must be 1–100")
    directory.mkdir(parents=True, exist_ok=False)
    schedule = [(label, i) for i in range(repetitions) for label in ("baseline", "candidate")]
    random.Random(baseline.get("seed", 42)).shuffle(schedule)
    report = {
        "version": 1,
        "status": "error",
        "order": schedule,
        "comparison_contract_sha256": compatibility(baseline),
        "runs": [],
    }
    save(directory / "comparison.json", report)
    results = {"baseline": [], "candidate": []}
    for label, repetition in schedule:
        config = copy.deepcopy(baseline if label == "baseline" else candidate)
        result = run(config, directory / f"{label}-{repetition + 1}", label)
        results[label].append(result)
        report["runs"].append(
            {
                "label": label,
                "repetition": repetition + 1,
                "run_id": result["run_id"],
                "status": result["status"],
                "logical_manifest_sha256": result["logical_manifest_sha256"],
            }
        )
        save(directory / "comparison.json", report)
    report["summary"] = {label: summarize(runs) for label, runs in results.items()}
    statuses = [r["status"] for runs in results.values() for r in runs]
    report["status"] = next((s for s in ("error", "inconclusive", "fail") if s in statuses), "pass")
    # Do not publish a performance winner if any planned repetition is unsuitable.
    implementations = {
        digest(
            {
                key: r.get(key)
                for key in (
                    "engine_source_sha256",
                    "adapter_implementation",
                    "checker_implementation",
                )
            }
        )
        for runs in results.values()
        for r in runs
    }
    # Different target adapters are allowed; the checker and engine must stay frozen.
    frozen = {
        digest({key: r.get(key) for key in ("engine_source_sha256", "checker_implementation")})
        for runs in results.values()
        for r in runs
    }
    report["implementation_fingerprints"] = sorted(implementations)
    report["performance_comparable"] = all(s == "pass" for s in statuses) and len(frozen) == 1
    if len(frozen) != 1:
        report["status"] = "error"
        report["reason"] = "Engine or checker implementation changed during comparison"
    report["interpretation"] = "Descriptive measurements only; target provenance is user-declared"
    save(directory / "comparison.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="streamscale.sh engine")
    commands = parser.add_subparsers(dest="command", required=True)
    single = commands.add_parser("run")
    single.add_argument("scenario", type=Path)
    single.add_argument("--output", type=Path, required=True)
    campaign = commands.add_parser("compare")
    campaign.add_argument("--baseline", type=Path, required=True)
    campaign.add_argument("--candidate", type=Path, required=True)
    campaign.add_argument("--output", type=Path, required=True)
    campaign.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run(json.loads(args.scenario.read_text()), args.output)
        else:
            result = compare(
                json.loads(args.baseline.read_text()),
                json.loads(args.candidate.read_text()),
                args.output,
                args.repetitions,
            )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        return EXIT["error"]
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT[result["status"]]


if __name__ == "__main__":

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
