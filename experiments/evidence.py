"""Read-only campaign audit, independent of Kubernetes and plotting dependencies."""

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = ("fixed", "cpu-hpa", "lag-keda")
SCENARIOS = ("steady", "flash-sale", "overload-recovery")


def read(path):
    return json.loads(path.read_text())


def seconds(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def audit(campaign, complete=True):
    state = read(campaign / "campaign.json")
    rows = state["rows"]
    if complete:
        require(state["status"] == "complete", "The measured campaign is incomplete")
    measured = [row for row in rows if row["phase"] == "measured"]
    expected = {
        (mode, scenario, repeat)
        for mode in MODES
        for scenario in SCENARIOS
        for repeat in range(1, 4)
    }
    cells = [(row["mode"], row["scenario"], row["repetition"]) for row in measured]
    require(len(cells) == len(set(cells)), "Duplicate measured cell")
    require(set(cells) <= expected, "Unexpected measured cell")
    if complete:
        require(set(cells) == expected, "All 27 measured cells are required")
    require(len({row["run_id"] for row in rows}) == len(rows), "Repeated run UUID")
    images, resources, groups = set(), set(), set()
    hashes = {scenario: set() for scenario in SCENARIOS}
    sample_count = 0
    for row in rows:
        require(
            row["performance_eligible"] == 1 and row["status"] == "passed",
            "An ineligible execution entered the accepted evidence",
        )
        directory = ROOT / row["artifact_directory"]
        require(directory.resolve().parent == campaign.resolve(), "Evidence outside campaign")
        checksums = read(directory / "checksums.json")
        for name, checksum in checksums.items():
            require(Path(name).name == name, "Unsafe evidence filename")
            require(
                hashlib.sha256((directory / name).read_bytes()).hexdigest() == checksum,
                f"Evidence checksum mismatch: {directory / name}",
            )
        require(read(directory / "summary.json") == row, "Campaign/raw summary mismatch")
        run = read(directory / "run.json")
        require(run["run_id"] == row["run_id"], "Raw run UUID differs from CSV")
        require(run["group_id"] not in groups, "Consumer group reused")
        groups.add(run["group_id"])
        require(run["image"] == state["image"]["image"], "Recorded image tag changed")
        require(row["image_digest"] == state["image"]["image_id"], "Recorded image digest changed")
        if row["phase"] == "measured":
            hashes[row["scenario"]].add(run["logical_sha256"])
        sql = read(directory / "sql-measurements.json")
        facts = sql["facts"]
        require(sql["run_id"] == row["run_id"], "SQL export belongs to another run")
        require(
            len(facts) == len({fact["event_id"] for fact in facts}) == row["unique_facts"],
            "SQL unique identities/count mismatch",
        )
        latencies = sorted(
            seconds(fact["ingested_at"]) - seconds(fact["produced_at"]) for fact in facts
        )
        require(latencies and latencies[0] >= 0, "Missing or negative SQL latencies")
        p95 = latencies[math.ceil(len(latencies) * 0.95) - 1]
        require(
            math.isclose(p95, row["p95_end_to_end_seconds"], abs_tol=1e-6),
            "p95 does not match raw SQL timestamps",
        )
        start, end = seconds(row["measurement_start"]), seconds(row["measurement_end"])
        throughput = sum(start <= seconds(fact["ingested_at"]) < end for fact in facts) / (
            end - start
        )
        require(
            math.isclose(throughput, row["throughput_window_per_second"], abs_tol=1e-6),
            "Throughput does not match the declared half-open SQL window",
        )
        for inspection in (
            read(directory / "before-restart-inspection.json"),
            read(directory / "restart.json")["inspection"],
        ):
            require(
                inspection["status"] == "passed" and inspection["stored_facts"] == len(facts),
                "Reconciliation/restart did not pass with the exported fact count",
            )
        for line in (directory / "samples.jsonl").read_text().splitlines():
            sample = json.loads(line)
            sample_count += 1
            require(sample["mode"] == row["mode"], "Sample controller mode changed")
            require(
                1 <= sample["desired"] <= (1 if row["mode"] == "fixed" else 3),
                "Desired replica count escaped its mode bounds",
            )
            for pod in sample["pods"]:
                require(
                    pod["spec"]["containers"][0]["image"] == run["image"],
                    "Actual consumer pod used another image",
                )
                resources.add(json.dumps(pod["spec"]["containers"][0]["resources"], sort_keys=True))
                for container in pod["status"].get("containerStatuses", []):
                    if container.get("imageID"):
                        images.add(container["imageID"])
    require(len(images) <= 1 and len(resources) <= 1, "Actual consumer image/resources changed")
    require(
        all(len(values) <= 1 for values in hashes.values()), "Scenario logical sequences differ"
    )
    if complete:
        with (campaign / "comparison.csv").open(newline="") as source:
            compared = list(csv.DictReader(source))
        require(
            {row["run_id"] for row in compared} == {row["run_id"] for row in measured}
            and len(compared) == 27,
            "Comparison CSV does not contain exactly the eligible matrix",
        )
        expected_rows = {
            row["run_id"]: {key: "" if value is None else str(value) for key, value in row.items()}
            for row in measured
        }
        require(
            all(row == expected_rows[row["run_id"]] for row in compared),
            "Comparison CSV values differ from verified raw summaries",
        )
    return {
        "status": "passed",
        "complete": complete,
        "measured_runs": len(measured),
        "accepted_executions": len(rows),
        "excluded_attempts": len(state["failures"]),
        "raw_samples": sample_count,
        "actual_consumer_image_ids": sorted(images),
        "consumer_resources": [json.loads(value) for value in sorted(resources)],
        "scenario_logical_sha256": {key: sorted(value) for key, value in hashes.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path)
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    campaign = args.campaign or Path(
        (ROOT / "artifacts/experiments/latest.path").read_text().strip()
    )
    print(json.dumps(audit(campaign, complete=not args.partial), indent=2))


if __name__ == "__main__":
    main()
