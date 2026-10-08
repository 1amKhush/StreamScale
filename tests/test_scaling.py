"""Rendered controller isolation and execution-group alignment."""

import subprocess
from pathlib import Path

import pytest
from streamscale_workload.generator import generate

ROOT = Path(__file__).resolve().parents[1]
HELM = ROOT / ".tools/bin/helm"


@pytest.mark.parametrize("mode", ["fixed", "cpu-hpa", "lag-keda"])
def test_chart_installs_only_its_selected_controller(mode):
    if not HELM.exists():
        pytest.skip("Install pinned Helm with make k8s-tools for chart checks")
    rendered = subprocess.run(
        [
            str(HELM),
            "template",
            "streamscale",
            str(ROOT / "deploy/k8s/chart"),
            "--set",
            f"scaling.mode={mode}",
            "--set",
            "run.name=unique-run",
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert rendered.count("kind: HorizontalPodAutoscaler") == (mode == "cpu-hpa")
    assert rendered.count("kind: ScaledObject") == (mode == "lag-keda")
    assert f"--mode {mode} --group-id streamscale.run.unique-run" in rendered
    consumer = next(
        document
        for document in rendered.split("---")
        if "kind: Deployment" in document and "name: streamscale-consumer" in document
    )
    assert ("  replicas: 1" in consumer) == (mode == "fixed")
    if mode == "lag-keda":
        assert "bootstrapServers: streamscale-kafka.default.svc.cluster.local:9092" in rendered
        assert "consumerGroup: streamscale.run.unique-run" in rendered
        assert "minReplicaCount: 1" in rendered and "maxReplicaCount: 3" in rendered
    elif mode == "cpu-hpa":
        assert "minReplicas: 1" in rendered and "maxReplicas: 3" in rendered
    assert "INTERNAL://streamscale-kafka.default.svc.cluster.local:9092" in rendered


@pytest.mark.parametrize(
    "setting", ["consumer.replicas=2", "scaling.maxReplicas=4", "scaling.mode=both"]
)
def test_chart_rejects_out_of_contract_scaling(setting):
    if not HELM.exists():
        pytest.skip("Install pinned Helm with make k8s-tools for chart checks")
    result = subprocess.run(
        [str(HELM), "template", "streamscale", str(ROOT / "deploy/k8s/chart"), "--set", setting],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0


def test_scaler_and_consumer_share_the_explicit_unique_run_group(tmp_path):
    run = generate(tmp_path / "run", orders=2, mode="lag-keda", group_id="streamscale.run.unique")
    assert run["group_id"] == "streamscale.run.unique"
    assert run["mode"] == "lag-keda"


def test_empty_group_is_rejected_before_creating_artifacts(tmp_path):
    with pytest.raises(ValueError, match="Consumer group"):
        generate(tmp_path / "run", group_id=" ")
    assert not (tmp_path / "run").exists()
