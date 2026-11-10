"""Preparation reuse must follow the services that determine calibrated capacity."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from experiments.predictive import campaign, tuning
from streamscale.predictive import DEFAULT_POLICY


@pytest.fixture
def environment(tmp_path, monkeypatch):
    files = (
        "uv.lock",
        "console/uv.lock",
        "experiments/predictive/uv.lock",
        "Dockerfile",
        "console/deploy/worker.Dockerfile",
    )
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    objects = {}
    for name, container in (
        ("streamscale-console-consumer", "consumer"),
        ("streamscale-console-agent", "agent"),
        ("streamscale-postgres", "postgres"),
        ("streamscale-kafka", "kafka"),
    ):
        objects[name] = {
            "metadata": {"resourceVersion": "1"},
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": container,
                                "image": f"{container}:original",
                                "resources": {"limits": {"cpu": "500m", "memory": "256Mi"}},
                            }
                        ]
                    }
                },
            },
        }
    objects["nodes"] = {
        "items": [
            {
                "status": {
                    "nodeInfo": dict.fromkeys(
                        (
                            "architecture",
                            "kernelVersion",
                            "kubeletVersion",
                            "containerRuntimeVersion",
                            "osImage",
                        ),
                        "original",
                    ),
                    "capacity": {"cpu": "8", "memory": "16Gi"},
                }
            }
        ]
    }

    def kubectl(*args):
        name = "nodes" if args[1] == "nodes" else args[2]
        return SimpleNamespace(stdout=json.dumps(objects[name]))

    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    monkeypatch.setattr(campaign, "kubectl", kubectl)
    monkeypatch.setattr(campaign, "versions", lambda: {"runtime": "locked"})
    return objects


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("streamscale-console-consumer", "cpu", "1"),
        ("streamscale-console-agent", "memory", "1Gi"),
        ("streamscale-postgres", "cpu", "2"),
        ("streamscale-kafka", "memory", "2Gi"),
        ("streamscale-postgres", "image", "postgres:changed"),
        ("streamscale-kafka", "image", "kafka:changed"),
    ],
)
def test_shared_capacity_or_worker_limits_invalidate_preparation(environment, name, field, value):
    original = campaign.fingerprint()
    container = environment[name]["spec"]["template"]["spec"]["containers"][0]
    if field == "image":
        container["image"] = value
    else:
        container["resources"]["limits"][field] = value
    assert campaign.fingerprint() != original


@pytest.mark.parametrize("field", ["cpu", "memory"])
def test_node_capacity_changes_invalidate_preparation(environment, field):
    original = campaign.fingerprint()
    environment["nodes"]["items"][0]["status"]["capacity"][field] = "changed"
    assert campaign.fingerprint() != original


@pytest.mark.parametrize("name", ["Dockerfile", "console/deploy/worker.Dockerfile"])
def test_runtime_build_changes_invalidate_preparation(environment, name):
    original = campaign.fingerprint()
    (campaign.ROOT / name).write_text("changed runtime build")
    assert campaign.fingerprint() != original


def test_replica_actions_metadata_and_worker_provenance_tags_do_not_invalidate(environment):
    original = campaign.fingerprint()
    for name in environment:
        if name == "nodes":
            continue
        environment[name]["spec"]["replicas"] = 12
        environment[name]["metadata"]["resourceVersion"] = "new"
        if name.startswith("streamscale-console-"):
            environment[name]["spec"]["template"]["spec"]["containers"][0]["image"] = (
                "worker:new-tag"
            )
    assert campaign.fingerprint() == original
    snapshot = campaign.environment_snapshot()
    assert snapshot["workloads"]["streamscale-postgres"]["postgres"]["image"] == "postgres:original"
    assert "image" not in snapshot["workloads"]["streamscale-console-consumer"]["consumer"]


@pytest.mark.parametrize("change_during_tuning", [False, True])
def test_environment_drift_never_publishes_an_accepted_artifact(
    tmp_path, monkeypatch, change_during_tuning
):
    async def retained(*args):
        return {"id": args[2]}

    async def tune(*args):
        return copy.deepcopy(DEFAULT_POLICY), {"status": "infeasible"}

    identities = iter(["identity", "changed"] if change_during_tuning else ["changed"])
    publications = []
    monkeypatch.setattr(campaign, "retained", retained)
    monkeypatch.setattr(campaign, "calibration", lambda runs: {"capacity": {1: 100}})
    monkeypatch.setattr(campaign, "train", lambda *a: ({}, {}))
    monkeypatch.setattr(campaign, "fingerprint", lambda: next(identities))
    monkeypatch.setattr(campaign, "environment_snapshot", lambda: {"environment": "original"})
    monkeypatch.setattr(
        campaign.shutil, "disk_usage", lambda *a: SimpleNamespace(free=10 * 1024**3)
    )
    monkeypatch.setattr(campaign, "publish", lambda folder, *a: publications.append(folder))
    monkeypatch.setattr(campaign, "install", lambda *a: None)
    monkeypatch.setattr(tuning, "tune", tune)
    with pytest.raises(RuntimeError, match="environment changed"):
        asyncio.run(campaign.prepare(None, tmp_path, "identity"))
    assert publications == ([tmp_path / "candidate"] if change_during_tuning else [])
    assert not (tmp_path / "model/current.json").exists()
