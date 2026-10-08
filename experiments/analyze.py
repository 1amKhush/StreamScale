"""Generate standalone scientific plots and a report from a complete audited matrix."""

import argparse
import csv
import hashlib
import json
import os
import shutil
import statistics
from pathlib import Path

from experiments.evidence import MODES, ROOT, SCENARIOS, audit, read, seconds

COLORS = {"fixed": "#3266a8", "cpu-hpa": "#cc7831", "lag-keda": "#2b896a"}
LABELS = {"fixed": "Fixed", "cpu-hpa": "CPU-HPA", "lag-keda": "Lag-KEDA"}
METRICS = (
    "throughput_window_per_second",
    "p95_end_to_end_seconds",
    "maximum_sampled_lag",
    "recovery_seconds",
    "scaling_response_seconds",
    "replica_seconds",
    "average_cpu_cores",
    "peak_memory_mib",
    "lag_sample_coverage",
    "max_ready_replicas",
)


def aggregate(rows):
    results = []
    for scenario in SCENARIOS:
        for mode in MODES:
            selected = [row for row in rows if row["scenario"] == scenario and row["mode"] == mode]
            if len(selected) != 3 or {row["repetition"] for row in selected} != {1, 2, 3}:
                raise RuntimeError("Each comparison requires three distinct measured repetitions")
            for metric in METRICS:
                values = [row[metric] for row in selected if row.get(metric) is not None]
                results.append(
                    {
                        "scenario": scenario,
                        "mode": mode,
                        "metric": metric,
                        "observed": len(values),
                        "missing": 3 - len(values),
                        "median": statistics.median(values) if values else None,
                        "minimum": min(values) if values else None,
                        "maximum": max(values) if values else None,
                    }
                )
    return results


def csv_output(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def value_table(aggregates, metric):
    return {(row["scenario"], row["mode"]): row for row in aggregates if row["metric"] == metric}


def display(row, digits=2):
    if row["median"] is None:
        return "unobserved"
    return f"{row['median']:.{digits}f} [{row['minimum']:.{digits}f}, {row['maximum']:.{digits}f}]"


def report(campaign, state, frozen, rows, aggregates, verification):
    throughput = value_table(aggregates, "throughput_window_per_second")
    latency = value_table(aggregates, "p95_end_to_end_seconds")
    lag = value_table(aggregates, "maximum_sampled_lag")
    replicas = value_table(aggregates, "replica_seconds")
    recovery = value_table(aggregates, "recovery_seconds")
    resources = verification["consumer_resources"][0]
    lines = [
        "# StreamScale measured results",
        "",
        f"Campaign `{campaign.name}` completed {state['ended_at']}. All **27 measured runs** "
        "passed acknowledgement/SQL/DLQ reconciliation before and after a controlled restart.",
        "",
        f"Current-campaign excluded attempts: **{len(state['failures'])}**. The independent audit "
        f"verified {verification['raw_samples']:,} raw samples, SQL metrics, CSV values, fresh "
        "consumer groups and unchanged actual consumer image/resource settings.",
        "",
        f"Protocol **v{frozen['protocol_version']}**: pilot capacity "
        f"{frozen['pilot_capacity_per_second']:.2f} accepted events/second; frozen steady rate "
        f"**{frozen['rates']['steady']}**, surge rate "
        f"**{frozen['rates']['surge']}** events/second. "
        "Each measured publishing window is 60 seconds, after a separate 30-second cache warmup. "
        "Each execution uses a fresh run UUID and consumer group. "
        "Measured group startup is included.",
        "",
        "Values below are **median [minimum, maximum] across three repetitions**. "
        "Latency is the median of three independently computed run p95 values, not a pooled "
        "percentile or an average of pod percentiles. No confidence interval or statistical "
        "significance is claimed.",
        "",
        "| Scenario | Mode | Window throughput (facts/s) | Run p95 latency (s) "
        "| Maximum sampled lag |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        for mode in MODES:
            key = (scenario, mode)
            lines.append(
                f"| {scenario} | {LABELS[mode]} | {display(throughput[key])} | "
                f"{display(latency[key])} | {display(lag[key], 0)} |"
            )
    lines += [
        "",
        "![Primary outcomes](primary-outcomes.svg)",
        "",
        "Dots are individual repetitions; diamonds are medians and whiskers span "
        "the observed range.",
        "",
    ]
    for scenario in SCENARIOS:
        best = min(MODES, key=lambda mode: latency[(scenario, mode)]["median"])
        lines.append(
            f"For **{scenario}**, {LABELS[best]} had the lowest observed median run p95 "
            f"({latency[(scenario, best)]['median']:.2f} seconds). The paired table shows "
            "the throughput and lag tradeoffs; this describes this local campaign only."
        )
        lines.append("")
    lines += [
        "| Scenario | Mode | Ready replica-seconds | Recovery after overload (s) "
        "| Observed recoveries |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        for mode in MODES:
            key = (scenario, mode)
            observed = recovery[key]["observed"] if scenario == "overload-recovery" else "n/a"
            recovered = display(recovery[key]) if scenario == "overload-recovery" else "n/a"
            lines.append(
                f"| {scenario} | {LABELS[mode]} | {display(replicas[key])} "
                f"| {recovered} | {observed} |"
            )
    lines += [
        "",
        "![Resource outcomes](resource-outcomes.svg)",
        "",
        "![Lag and ready replica trajectories](trajectories.svg)",
        "",
        "Trajectory lines show each repetition separately, aligned to publication start; "
        "shading indicates the scheduled surge and the vertical line the end of publication. "
        "Lines continue through confirmed drain. Ready pods differ from desired replicas "
        "and Kafka members.",
        "",
        "The primary throughput counts unique facts ingested in the half-open 60-second window. "
        "It can be lower during a burst even when every event eventually arrives. "
        "`summary.csv` also reports processing-span and drain-inclusive throughput. "
        "Latency uses every unique accepted event, including the drained tail. Maximum lag is "
        "the maximum sampled sum of three broker-offset partitions. Recovery ends after five "
        "distinct fresh zero-lag observations, including their confirmation delay. "
        "Missing recovery "
        "or scaling response remains missing; it is never filled with zero.",
        "",
        "Resource integrals cover publication through confirmed drain, before the controlled "
        "restart. They use actual observation times with left-step interpolation. Kubernetes CPU "
        "metrics are delayed averages, sampled every five seconds; missing leading measurements "
        "are disclosed by coverage columns. Maximum memory is a sampled sum across available "
        "consumer pods. These resource values are approximations, not billing measurements.",
        "",
        f"Image `{state['image']['image']}`; build digest `{state['image']['image_id']}`; "
        f"Git `{state['image']['git_commit']}`; dirty build: `{state['image']['git_dirty']}`. "
        "The platform manifest IDs observed in pods are separately preserved in `audit.json`.",
        "",
        f"Consumer requests: {resources['requests']['cpu']} CPU / "
        f"{resources['requests']['memory']}; limits: {resources['limits']['cpu']} CPU / "
        f"{resources['limits']['memory']} per replica. Fixed uses "
        "one replica; CPU-HPA targets 60% of requested CPU; KEDA targets 100 Kafka records of "
        "lag per replica. Autoscaled modes are bounded at 1–3, with three input partitions. "
        "Controllers are alternative, never simultaneously attached to the consumer.",
        "",
        "The same logical scenario manifest must match across all nine corresponding cells. "
        "Seed 4242 randomizes cell order. All rates/windows were frozen before measurement; "
        "no measured run was tuned individually. Run IDs, logical hashes, correctness outcomes "
        "and exact configuration appear in the companion CSV/protocol/provenance files.",
        "",
        "The single kind node shares host CPU with Kafka, PostgreSQL, monitoring and the observer. "
        "The host, retained database growth, cache carryover, short windows, metrics/controller "
        "cadence and fresh-group startup may affect results. Randomized order reduces but does "
        "not eliminate carryover. Three repetitions give a small descriptive sample. Sampled "
        "peaks can miss intermediate maxima. This v2 60-second calibration is not directly "
        "comparable to the original 120-second fixed-rate proposal. The controlled restart "
        "occurs after measurement; crash-during-commit replay was separately exercised in M3/M4. "
        "The pipeline remains at-least-once, and DLQ redelivery can create extra copies. "
        "No production, multi-node or exactly-once improvement is claimed.",
        "",
        "Reproduce with `./streamscale.sh all --no-ui`; reuse validates retained raw evidence. "
        "For new measurements use `./streamscale.sh experiments --fresh`, then "
        "`./streamscale.sh analysis`. Raw artifacts are ignored locally and identified by "
        "`artifacts/experiments/latest.path`. See [the protocol](../../experiments.md) and "
        "[presentation walkthrough](../../presentation.md).",
        "",
    ]
    return "\n".join(lines)


def plots(rows, frozen, destination):
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "artifacts/matplotlib-cache"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "svg.hashsalt": "streamscale-v2",
        }
    )
    figures = []

    def outcomes(name, metrics):
        figure, axes = plt.subplots(len(metrics), 3, figsize=(11.7, 8.3), layout="constrained")
        for column, scenario in enumerate(SCENARIOS):
            for index, (metric, label) in enumerate(metrics):
                axis = axes[index, column]
                for position, mode in enumerate(MODES):
                    points = [
                        row[metric]
                        for row in sorted(rows, key=lambda row: row["repetition"])
                        if row["scenario"] == scenario
                        and row["mode"] == mode
                        and row.get(metric) is not None
                    ]
                    if not points:
                        continue
                    median = statistics.median(points)
                    axis.scatter(
                        [position + (i - 1) * 0.13 for i in range(len(points))],
                        points,
                        color=COLORS[mode],
                        s=22,
                        alpha=0.7,
                    )
                    axis.errorbar(
                        position,
                        median,
                        yerr=[[median - min(points)], [max(points) - median]],
                        fmt="D",
                        color=COLORS[mode],
                        capsize=5,
                        markersize=5,
                    )
                axis.set_xticks(range(3), [LABELS[mode] for mode in MODES])
                axis.set_ylabel(label)
                axis.set_ylim(bottom=0)
                axis.grid(axis="y", alpha=0.2)
                if index == 0:
                    axis.set_title(scenario.replace("-", " ").title())
        figure.suptitle("StreamScale · " + name.replace("-", " ") + " · n=3 per cell", fontsize=14)
        for suffix in ("svg", "png"):
            figure.savefig(
                destination / f"{name}.{suffix}",
                dpi=160,
                metadata={"Date": None} if suffix == "svg" else None,
            )
        figures.append(figure)

    outcomes(
        "primary-outcomes",
        [
            ("throughput_window_per_second", "Window throughput (unique facts/s)"),
            ("p95_end_to_end_seconds", "Per-run p95 end-to-end latency (s)"),
            ("maximum_sampled_lag", "Maximum sampled lag (records)"),
        ],
    )
    outcomes(
        "resource-outcomes",
        [
            ("replica_seconds", "Ready replica-seconds (including drain)"),
            ("average_cpu_cores", "Average observed consumer CPU (cores)"),
            ("peak_memory_mib", "Peak sampled consumer memory (MiB)"),
        ],
    )
    figure, axes = plt.subplots(3, 2, figsize=(11.7, 8.3), layout="constrained")
    for index, scenario in enumerate(SCENARIOS):
        for row in rows:
            if row["scenario"] != scenario:
                continue
            source = ROOT / row["artifact_directory"] / "samples.jsonl"
            samples = [json.loads(line) for line in source.read_text().splitlines()]
            start = seconds(row["measurement_start"])
            samples = [sample for sample in samples if sample["time"] >= start]
            lag = [sample for sample in samples if sample["lag"] is not None]
            label = LABELS[row["mode"]] if row["repetition"] == 1 else None
            axes[index, 0].plot(
                [sample["time"] - start for sample in lag],
                [sum(sample["lag"].values()) for sample in lag],
                color=COLORS[row["mode"]],
                alpha=0.65,
                linewidth=1,
                label=label,
            )
            axes[index, 1].step(
                [sample["time"] - start for sample in samples],
                [sample["ready"] for sample in samples],
                where="post",
                color=COLORS[row["mode"]],
                alpha=0.65,
                linewidth=1,
                label=label,
            )
        for column in range(2):
            axis = axes[index, column]
            elapsed = 0
            for duration, rate in frozen["scenarios"][scenario]["schedule"]:
                if rate == frozen["rates"]["surge"]:
                    axis.axvspan(elapsed, elapsed + duration, color="#9d8096", alpha=0.12)
                elapsed += duration
            axis.axvline(60, color="#555555", linestyle="--", linewidth=0.8)
            axis.set_title(scenario.replace("-", " ").title())
            axis.set_xlabel("Seconds since publication began")
            axis.set_ylabel("Sampled lag (records)" if column == 0 else "Ready consumers")
            axis.set_ylim(bottom=0)
            axis.grid(alpha=0.15)
        axes[index, 1].set_yticks([0, 1, 2, 3])
        axes[index, 1].set_ylim(-0.1, 3.2)
    axes[0, 0].legend(frameon=False, ncols=3, fontsize=8)
    figure.suptitle("StreamScale · every measured trajectory · three repetitions", fontsize=14)
    for suffix in ("svg", "png"):
        figure.savefig(
            destination / f"trajectories.{suffix}",
            dpi=160,
            metadata={"Date": None} if suffix == "svg" else None,
        )
    figures.append(figure)
    with PdfPages(
        destination / "presentation.pdf",
        metadata={
            "Title": "StreamScale measured scaling comparison",
            "CreationDate": None,
            "ModDate": None,
            "Author": "StreamScale",
        },
    ) as pdf:
        introduction = plt.figure(figsize=(11.7, 8.3))
        introduction.text(0.08, 0.86, "StreamScale", fontsize=30, weight="bold")
        introduction.text(
            0.08, 0.78, "Kafka → transactional PostgreSQL · scaling experiment", fontsize=17
        )
        introduction.text(
            0.08,
            0.62,
            "27 measured runs · 3 modes × 3 scenarios × 3 repetitions\n"
            "All acknowledged IDs reconciled before/after restart\n"
            "Same image, logical scenario sequence, resources and partitions\n"
            "30-second warmup + 60-second publishing window per cell\n"
            f"Frozen rates: {frozen['rates']['steady']}/s steady · {frozen['rates']['surge']}/s surge",
            fontsize=14,
            linespacing=1.8,
            va="top",
        )
        introduction.text(
            0.08,
            0.30,
            "Dots = repetitions; diamonds = median; whiskers = observed range\n"
            "SQL p95 includes the drained tail; throughput uses the publishing window\n"
            "One shared kind node, small sample, sampled resources; descriptive results\n"
            "See report.md, summary.csv, frozen-protocol.json and provenance.json",
            fontsize=12,
            linespacing=1.8,
            va="top",
        )
        pdf.savefig(introduction)
        plt.close(introduction)
        for figure in figures:
            pdf.savefig(figure)
            plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/results/current")
    args = parser.parse_args()
    campaign = args.campaign or Path(
        (ROOT / "artifacts/experiments/latest.path").read_text().strip()
    )
    verification = audit(campaign)
    state = read(campaign / "campaign.json")
    frozen = read(campaign / "frozen-protocol.json")
    rows = [
        row | {"peak_memory_mib": row["peak_memory_bytes"] / 1024**2}
        for row in state["rows"]
        if row["phase"] == "measured"
    ]
    aggregates = aggregate(rows)
    destination = args.output
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("summary.csv", "comparison.csv", "frozen-protocol.json", "failed-attempts.csv"):
        shutil.copy2(campaign / name, destination / name)
    (destination / "audit.json").write_text(json.dumps(verification, indent=2) + "\n")
    csv_output(destination / "aggregate.csv", aggregates)
    environment = read(campaign / "environment.json")
    provenance = {
        "campaign": campaign.name,
        "started_at": state["started_at"],
        "ended_at": state["ended_at"],
        "image": state["image"],
        "fingerprint": state["fingerprint"],
        "host": environment["docker"],
        "node_versions": [
            {
                key: node["status"]["nodeInfo"][key]
                for key in (
                    "kubeletVersion",
                    "kernelVersion",
                    "osImage",
                    "containerRuntimeVersion",
                    "architecture",
                )
            }
            for node in environment["nodes"]["items"]
        ],
        "source_artifact_directory": str(campaign.relative_to(ROOT)),
        "source_checksums": {
            name: hashlib.sha256((campaign / name).read_bytes()).hexdigest()
            for name in ("campaign.json", "frozen-protocol.json", "summary.csv", "comparison.csv")
        },
    }
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    text = report(campaign, state, frozen, rows, aggregates, verification)
    (destination / "report.md").write_text(text)
    plots(rows, frozen, destination)
    print(
        f"M8 report: {destination / 'report.md'}\nPresentation: {destination / 'presentation.pdf'}"
    )


if __name__ == "__main__":
    main()
