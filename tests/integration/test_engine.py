"""Real broker/SQL acceptance, including a fast-looking but lossy worker."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from confluent_kafka.admin import AdminClient, NewTopic

from streamscale.engine.runner import run, save

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_independent_worker_and_loss_regression(settings):
    identity = uuid4().hex
    directory = ROOT / "artifacts/engine" / f"acceptance-{identity}"
    topic = f"streamscale.telemetry.{identity}"
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    admin.create_topics([NewTopic(topic, num_partitions=3, replication_factor=1)])[topic].result(15)
    environment = os.environ | {
        "KAFKA_BOOTSTRAP_SERVERS": settings.kafka_bootstrap_servers,
        "DATABASE_URL": settings.database_url,
    }
    # The example owns its tables. Nothing references the original order schema.
    with psycopg.connect(settings.database_url, connect_timeout=5) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS telemetry_reading (
            sample_id text PRIMARY KEY, experiment text NOT NULL, reading jsonb NOT NULL,
            completed_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    base = json.loads((ROOT / "examples/telemetry/scenario.json").read_text())
    base["options"]["topic"] = topic
    results = []
    for label, drop in (("baseline", 0), ("candidate", 5)):
        config = copy.deepcopy(base)
        config["target"]["revision"] = label
        worker = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "examples/telemetry/worker.py"),
                "--topic",
                topic,
                "--group",
                f"engine-{uuid4()}",
                "--drop-every",
                str(drop),
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            result = run(config, directory / label, label)
            assert worker.poll() is None, "Independent worker exited unexpectedly"
            results.append(result)
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
    baseline, candidate = results
    save(
        directory / "acceptance.json",
        {
            "expected": {"baseline": "pass", "candidate": "fail"},
            "observed": {"baseline": baseline["status"], "candidate": candidate["status"]},
            "topic": topic,
            "evidence": [
                str(directory / label / "result.json") for label in ("baseline", "candidate")
            ],
        },
    )
    assert baseline["status"] == "pass", baseline
    assert candidate["status"] == "fail", candidate
    assert baseline["logical_manifest_sha256"] == candidate["logical_manifest_sha256"]
    assert candidate["checks"]["missing"]
    assert not candidate["eligible_for_comparison"]
    # Retain topic, rows and run evidence. No global cleanup of services or data.
