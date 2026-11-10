"""Standalone scientific figures from the read-only predictive analysis JSON."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

COLORS = {"actual": "#334155", "median": "#2563eb", "upper": "#d97706"}


def plot(source, output):
    data = json.loads(source.read_text())
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.18,
            "svg.hashsalt": "streamscale-predictive-analysis",
        }
    )

    def save(figure, name):
        figure.savefig(output / f"{name}.svg", bbox_inches="tight", metadata={"Date": None})
        figure.savefig(output / f"{name}.png", dpi=160, bbox_inches="tight")
        plt.close(figure)

    figure, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
    families = ("steady", "ramp", "repeated-burst", "abrupt-step")
    for axis, family in zip(axes.flat, families, strict=True):
        row = data["validation_traces"][family]
        end = [t + row["horizon"] for t in row["origin_seconds"]]
        for field, label, color in (
            ("actual_horizon_rate", "Actual interval mean", COLORS["actual"]),
            ("median_rate", "Median forecast", COLORS["median"]),
            ("upper_rate", "Upper forecast (sum of partition q90)", COLORS["upper"]),
        ):
            axis.plot(end, row[field], label=label, color=color, linewidth=1.6)
        axis.axvline(row["publication_end_seconds"], color="#64748b", linestyle=":", alpha=0.7)
        axis.set(
            title=family.replace("-", " ").title(),
            ylabel="Events/s",
            xlabel="Forecast interval end (s)",
        )
        axis.set_ylim(bottom=0)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="outside lower center", ncol=3, frameon=False)
    figure.suptitle(
        "Frozen forecaster: 20-second validation forecasts aligned with actual arrivals"
    )
    save(figure, "validation-forecasts")

    figure, axis = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    for index, (label, slice_name, color) in enumerate(
        (("All windows", "all", "#2563eb"), ("Publishing origins", "publishing_origin", "#d97706"))
    ):
        rows = [
            next(
                r
                for r in data["validation"]
                if r["family"] == family
                and r["horizon"] == 20
                and r["quantile"] == 0.9
                and r["slice"] == slice_name
            )
            for family in families
        ]
        values = [r["live_partition"]["coverage_including_ties"] * 100 for r in rows]
        positions = [x + (index - 0.5) * 0.36 for x in range(4)]
        bars = axis.bar(positions, values, width=0.34, label=label, color=color)
        axis.bar_label(bars, fmt="%.1f%%", fontsize=8)
    axis.axhline(90, linestyle="--", color="#475569", label="Nominal per-partition q90")
    axis.set_xticks(range(4), [f.replace("-", "\n") for f in families])
    axis.set(
        ylim=(0, 112),
        ylabel="Actual ≤ upper forecast (%)",
        title="Coverage changes when easy drain windows are removed",
    )
    axis.legend(loc="lower left", frameon=False)
    save(figure, "conditional-coverage")

    figure, axis = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    rows = [r for r in data["past_only_probes"] if r["past_pattern"].startswith("constant_")]
    rates = [r["aggregate_features"]["rate_5s"] for r in rows]
    axis.plot(rates, rates, color="#94a3b8", linestyle=":", label="Recent rate (reference)")
    for key, name, color in (
        ("median_rate", "Median forecast", COLORS["median"]),
        ("upper_rate", "Upper forecast", COLORS["upper"]),
    ):
        axis.plot(rates, [r[key] for r in rows], marker="o", color=color, label=name)
    axis.set(
        xlabel="Constant recent arrival rate for 81s (events/s)",
        ylabel="Forecast rate (events/s)",
        title="Past-only probes: learned response is not monotonic",
    )
    axis.legend(frameon=False)
    save(figure, "constant-history-probes")
    print(f"Predictive diagnostic figures saved to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.input
    if source is None:
        pointer = Path("artifacts/predictive/current.json")
        if not pointer.exists():
            raise SystemExit("Run ./streamscale.sh predictive-prepare first")
        source = (
            pointer.parent
            / json.loads(pointer.read_text())["fingerprint"]
            / "analysis/analysis.json"
        )
    plot(source, args.output or source.parent)


if __name__ == "__main__":
    main()
