"""M9 live acceptance: real cluster state, explicit disconnection and scoped access."""

import json
import time
import urllib.error
import urllib.request

import pytest
from streamscale_console.cluster import AgentForward, kubectl

from .conftest import kube_json, wait_idle

pytestmark = pytest.mark.live

SERVICE_ACCOUNT = "system:serviceaccount:streamscale:streamscale-console-agent"


def test_real_cluster_state_reaches_the_console(console):
    state = console.state()
    sample = state["sample"]
    assert state["status"]["agent"] == "connected"
    assert all(source["ok"] for source in sample["sources"].values() if source)
    partitions = sample["kafka"]["partitions"]
    assert [row["partition"] for row in partitions] == [0, 1, 2]
    assert all(row["high"] >= 0 for row in partitions)
    pods = kube_json("get", "pods", "-l", "app=streamscale-console-consumer")["items"]
    live_pods = {
        pod["metadata"]["name"] for pod in pods if not pod["metadata"].get("deletionTimestamp")
    }
    assert {pod["name"] for pod in sample["kubernetes"]["pods"]} == live_pods
    infrastructure = sample["kubernetes"]["infrastructure"]
    assert infrastructure["kafka"]["ready"] == 1 and infrastructure["postgres"]["ready"] == 1
    deployment = kube_json("get", "deployment", "streamscale-console-consumer")
    assert sample["kubernetes"]["consumer"]["desired"] == deployment["spec"]["replicas"]


def test_agent_loss_is_shown_as_unreachable_then_recovers(console):
    wait_idle(console)  # never interrupt another test's sale
    before = console.state()["points"][-1]["sequence"]
    kubectl("scale", "deployment/streamscale-console-agent", "--replicas=0")
    try:
        state = console.wait(
            lambda state: state["status"]["agent"] == "unreachable",
            timeout=90,
            describe="the agent to be reported unreachable",
        )
        # The last known values stay visible but no new sample is fabricated.
        stalled = state["points"][-1]["received_at"]
        time.sleep(4)
        assert console.state()["points"][-1]["received_at"] == stalled
    finally:
        kubectl("scale", "deployment/streamscale-console-agent", "--replicas=1")
        kubectl("rollout", "status", "deployment/streamscale-console-agent", "--timeout=180s")
    recovered = console.wait(
        lambda state: (
            state["status"]["agent"] == "connected" and state["points"][-1]["received_at"] > stalled
        ),
        timeout=180,
        describe="the agent connection to recover",
    )
    assert recovered["status"]["forward_restarts"] >= 1
    assert before is not None


@pytest.mark.parametrize(
    ("verb", "resource", "allowed"),
    [
        ("patch", "deployments/streamscale-console-consumer --subresource=scale", True),
        ("patch", "deployments/streamscale-consumer --subresource=scale", False),
        ("delete", "deployments/streamscale-consumer", False),
        ("patch", "statefulsets/streamscale-kafka", False),
        ("patch", "statefulsets/streamscale-postgres --subresource=scale", True),
        ("patch", "statefulsets/streamscale-grafana --subresource=scale", False),
        ("delete", "horizontalpodautoscalers.autoscaling/streamscale-cpu", False),
        ("delete", "horizontalpodautoscalers.autoscaling/streamscale-console-cpu", True),
        ("delete", "scaledobjects.keda.sh/streamscale-consumer", False),
        ("delete", "secrets/streamscale-local-credentials", False),
        ("get", "pods --namespace kube-system", False),
    ],
)
def test_agent_role_names_its_targets(verb, resource, allowed):
    arguments = ["auth", "can-i", verb, *resource.split(), "--as", SERVICE_ACCOUNT]
    result = kubectl(*arguments, check=False)
    assert (result.stdout.strip() == "yes") is allowed, result.stdout + result.stderr


def test_agent_refuses_requests_without_the_console_token():
    forward = AgentForward()
    url = forward.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(url + "/v1/telemetry", timeout=10)
        assert rejected.value.code == 401
        with urllib.request.urlopen(url + "/health", timeout=10) as response:
            assert json.loads(response.read()) == {"status": "ok"}
    finally:
        forward.stop()


def test_unknown_operations_are_refused_by_the_console(console):
    refused = console.submit("cluster.delete", {"name": "streamscale"})
    assert refused["status"] == 409
