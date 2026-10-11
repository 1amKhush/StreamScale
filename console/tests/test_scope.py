"""The agent's Kubernetes calls reject anything outside approved console resources."""

import io
import json
import urllib.error

import pytest
from streamscale_console_worker.kube import (
    CONSUMER,
    CPU_HPA,
    SCALED_OBJECT,
    Kube,
    ScopeError,
    cpu_cores,
    memory_bytes,
)


class FakeApi:
    def __init__(self, pods=None):
        self.calls = []
        self.pods = pods or {}

    def __call__(self, request, timeout):
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url, body))
        path = request.full_url.removeprefix("https://api")
        if request.get_method() == "GET" and "/pods/" in path:
            name = path.rsplit("/", 1)[1]
            if name not in self.pods:
                raise urllib.error.HTTPError(
                    request.full_url, 404, "not found", {}, io.BytesIO(b'{"message":"nf"}')
                )
            return io.BytesIO(json.dumps(self.pods[name]).encode())
        return io.BytesIO(b"{}")


def pod(name, app, container="consumer", request="100m"):
    return {
        "metadata": {"name": name, "labels": {"app": app}},
        "spec": {"containers": [{"name": container, "resources": {"requests": {"cpu": request}}}]},
    }


@pytest.fixture
def kube(tmp_path):
    (tmp_path / "token").write_text("token\n")
    (tmp_path / "ca.crt").write_text("unused")
    api = FakeApi(
        {
            "streamscale-console-consumer-abc": pod(
                "streamscale-console-consumer-abc", "streamscale-console-consumer"
            ),
            "streamscale-consumer-xyz": pod("streamscale-consumer-xyz", "streamscale-consumer"),
            "streamscale-kafka-0": pod("streamscale-kafka-0", "streamscale-kafka", "kafka"),
            "streamscale-postgres-0": pod(
                "streamscale-postgres-0", "streamscale-postgres", "postgres"
            ),
        }
    )
    client = Kube(base="https://api", account=tmp_path, opener=api)
    client.api = api
    return client


def mutations(kube):
    return [call for call in kube.api.calls if call[0] != "GET"]


@pytest.mark.parametrize(
    "name", ["streamscale-consumer-xyz", "streamscale-kafka-0", "streamscale-postgres-0"]
)
def test_restart_refuses_pods_that_are_not_console_consumers(kube, name):
    with pytest.raises(ScopeError):
        kube.delete_console_pod(name)
    assert not mutations(kube)


def test_restart_deletes_only_a_verified_console_consumer(kube):
    kube.delete_console_pod("streamscale-console-consumer-abc")
    assert mutations(kube) == [
        (
            "DELETE",
            "https://api/api/v1/namespaces/streamscale/pods/streamscale-console-consumer-abc",
            None,
        )
    ]


@pytest.mark.parametrize("replicas", [-1, 4, 1.5, "2", True])
def test_console_replicas_stay_within_zero_to_three(kube, replicas):
    with pytest.raises(ScopeError):
        kube.scale_consumers(replicas)
    assert not mutations(kube)


def test_scaling_targets_the_console_deployment_only(kube):
    kube.scale_consumers(3)
    method, url, body = mutations(kube)[0]
    assert method == "PATCH"
    assert url.endswith(f"/apis/apps/v1/namespaces/streamscale/deployments/{CONSUMER}/scale")
    assert body == {"spec": {"replicas": 3}}


@pytest.mark.parametrize("role", ["consumer", "grafana", "streamscale-kafka", "../kafka"])
def test_outages_only_name_kafka_or_postgres(kube, role):
    with pytest.raises(ScopeError):
        kube.scale_infrastructure(role, 0)


@pytest.mark.parametrize("replicas", [2, -1, 3])
def test_infrastructure_outage_never_scales_beyond_one(kube, replicas):
    with pytest.raises(ScopeError):
        kube.scale_infrastructure("postgres", replicas)


def test_cpu_restriction_rejects_measured_and_broker_pods(kube):
    for name in ("streamscale-consumer-xyz", "streamscale-kafka-0"):
        with pytest.raises(ScopeError):
            kube.resize_cpu(name, "100m")
    assert not mutations(kube)


def test_cpu_restriction_lowers_request_with_a_lower_limit(kube):
    kube.resize_cpu("streamscale-postgres-0", "50m")
    method, url, body = mutations(kube)[0]
    assert url.endswith("/pods/streamscale-postgres-0/resize")
    assert body["spec"]["containers"][0] == {
        "name": "postgres",
        "resources": {"limits": {"cpu": "50m"}, "requests": {"cpu": "50m"}},
    }


@pytest.mark.parametrize("limit", ["5m", "2000m", "0.5", "1", "100"])
def test_cpu_restriction_requires_bounded_millicores(kube, limit):
    with pytest.raises(ScopeError):
        kube.resize_cpu("streamscale-console-consumer-abc", limit)


def test_autoscalers_must_be_named_console_resources(kube):
    target = {"name": CONSUMER, "kind": "Deployment", "apiVersion": "apps/v1"}
    with pytest.raises(ScopeError):
        kube.apply_cpu_hpa(
            {"metadata": {"name": "streamscale-cpu"}, "spec": {"scaleTargetRef": target}}
        )
    with pytest.raises(ScopeError):
        kube.apply_scaled_object(
            {"metadata": {"name": "streamscale-consumer"}, "spec": {"scaleTargetRef": target}}
        )
    with pytest.raises(ScopeError):
        kube.apply_cpu_hpa(
            {
                "metadata": {"name": CPU_HPA},
                "spec": {"scaleTargetRef": target | {"name": "streamscale-consumer"}},
            }
        )
    assert not mutations(kube)
    assert SCALED_OBJECT == "streamscale-console-consumer"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/namespaces/kube-system/pods",
        "/api/v1/pods",
        "/apis/apps/v1/deployments",
        "/apis/apps/v1/namespaces/other/deployments/x",
    ],
)
def test_requests_outside_the_namespace_are_refused(kube, path):
    with pytest.raises(ScopeError):
        kube.request("GET", path)
    assert not kube.api.calls


@pytest.mark.parametrize("name", ["UPPER", "a/b", "../x", "", "x" * 254])
def test_names_must_be_plain_dns_names(kube, name):
    with pytest.raises(ScopeError):
        kube.pod(name)


def test_quantities():
    assert cpu_cores("250m") == 0.25
    assert cpu_cores("1500000n") == pytest.approx(0.0015)
    assert cpu_cores("2") == 2
    assert memory_bytes("128Mi") == 128 * 2**20
    assert memory_bytes("1Gi") == 2**30
