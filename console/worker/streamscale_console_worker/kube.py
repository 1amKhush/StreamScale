"""In-cluster Kubernetes calls restricted to named StreamScale console resources.

The agent's Role is the outer fence; these functions are the inner one. Every
mutation names its target from a fixed allowlist or verifies a pod's console label
before acting, so a request can never reach another project's resources.
"""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NAMESPACE = "streamscale"
ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"

CONSUMER = "streamscale-console-consumer"
CONSUMER_APP = "streamscale-console-consumer"
AGENT = "streamscale-console-agent"
CPU_HPA = "streamscale-console-cpu"
SCALED_OBJECT = "streamscale-console-consumer"
KEDA_HPA = "keda-hpa-" + SCALED_OBJECT
INFRASTRUCTURE = {"kafka": "streamscale-kafka", "postgres": "streamscale-postgres"}
POSTGRES_POD = "streamscale-postgres-0"
MAX_CONSOLE_REPLICAS = 3


class ScopeError(ValueError):
    """A request named a resource outside the console's approved scope."""


class KubeError(RuntimeError):
    def __init__(self, status, reason):
        super().__init__(f"Kubernetes API {status}: {reason}")
        self.status = status


class Kube:
    def __init__(self, base=API, account=ACCOUNT, opener=None):
        self.base = base
        self.account = account
        self.opener = opener
        self._token = None
        self._token_read = 0.0
        self._context = None

    def _headers(self, content_type=None):
        # Projected service-account tokens rotate; reread the file periodically.
        if self._token is None or time.monotonic() - self._token_read > 60:
            self._token = (self.account / "token").read_text().strip()
            self._token_read = time.monotonic()
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _ssl(self):
        if self._context is None:
            self._context = ssl.create_default_context(cafile=str(self.account / "ca.crt"))
        return self._context

    def request(self, method, path, body=None, content_type="application/json", timeout=5):
        if not path.startswith(("/api/v1/namespaces/" + NAMESPACE + "/", "/apis/")):
            raise ScopeError("Only namespaced StreamScale API paths are permitted")
        if path.startswith("/apis/") and f"/namespaces/{NAMESPACE}/" not in path:
            raise ScopeError("Only the streamscale namespace is permitted")
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers=self._headers(content_type if data is not None else None),
        )
        try:
            if self.opener is not None:
                response = self.opener(request, timeout)
            else:
                response = urllib.request.urlopen(request, timeout=timeout, context=self._ssl())
            with response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            try:
                reason = json.loads(detail).get("message", detail)
            except json.JSONDecodeError:
                reason = detail
            raise KubeError(error.code, reason[:300]) from None
        return json.loads(payload) if payload else {}

    # Reads -----------------------------------------------------------------

    def console_pods(self):
        selector = urllib.parse.quote("app=" + CONSUMER_APP)
        return self.request("GET", f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector={selector}")[
            "items"
        ]

    def pod(self, name):
        return self.request("GET", f"/api/v1/namespaces/{NAMESPACE}/pods/{_name(name)}")

    def consumer_deployment(self):
        return self.request("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{CONSUMER}")

    def statefulset(self, role):
        name = _infrastructure(role)
        return self.request("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/statefulsets/{name}")

    def infrastructure_pods(self):
        selector = urllib.parse.quote("app in (" + ",".join(sorted(INFRASTRUCTURE.values())) + ")")
        return self.request("GET", f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector={selector}")[
            "items"
        ]

    def autoscalers(self):
        """HPAs and ScaledObjects that target the console consumer Deployment."""
        hpas = self.request(
            "GET", f"/apis/autoscaling/v2/namespaces/{NAMESPACE}/horizontalpodautoscalers"
        )["items"]
        try:
            scaled = self.request(
                "GET", f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects"
            )["items"]
        except KubeError as error:
            if error.status != 404:
                raise
            scaled = []
        return (
            [row for row in hpas if row["spec"]["scaleTargetRef"]["name"] == CONSUMER],
            [row for row in scaled if row["spec"]["scaleTargetRef"]["name"] == CONSUMER],
        )

    def pod_metrics(self):
        return self.request("GET", f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods")[
            "items"
        ]

    def events(self, limit=60):
        return self.request("GET", f"/api/v1/namespaces/{NAMESPACE}/events?limit={limit}")["items"]

    # Mutations -------------------------------------------------------------

    def scale_consumers(self, replicas):
        if type(replicas) is not int or not 0 <= replicas <= MAX_CONSOLE_REPLICAS:
            raise ScopeError("Console consumer replicas must be an integer from 0 to 3")
        return self.request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{CONSUMER}/scale",
            {"spec": {"replicas": replicas}},
            "application/merge-patch+json",
        )

    def scale_infrastructure(self, role, replicas):
        name = _infrastructure(role)
        if replicas not in (0, 1):
            raise ScopeError("Infrastructure outages may only scale between zero and one")
        return self.request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{NAMESPACE}/statefulsets/{name}/scale",
            {"spec": {"replicas": replicas}},
            "application/merge-patch+json",
        )

    def delete_console_pod(self, name, grace_seconds=None):
        pod = self.require_console_pod(name)
        query = "" if grace_seconds is None else f"?gracePeriodSeconds={int(grace_seconds)}"
        return self.request(
            "DELETE", f"/api/v1/namespaces/{NAMESPACE}/pods/{pod['metadata']['name']}{query}"
        )

    def resize_cpu(self, name, limit, request=None):
        """In-place CPU limit change; only console consumers and the PostgreSQL pod."""
        if name != POSTGRES_POD:
            self.require_console_pod(name)
        container = "postgres" if name == POSTGRES_POD else "consumer"
        _millicore_text(limit)
        pod = self.pod(name)
        current = next(
            (
                row["resources"].get("requests", {}).get("cpu")
                for row in pod["spec"]["containers"]
                if row["name"] == container
            ),
            None,
        )
        resources = {"limits": {"cpu": limit}}
        if request is not None:
            _millicore_text(request)
            resources["requests"] = {"cpu": request}
        elif current is not None and _millicores(current) > int(limit[:-1]):
            # A limit below the request is invalid, so lower the request with it.
            resources["requests"] = {"cpu": limit}
        return self.request(
            "PATCH",
            f"/api/v1/namespaces/{NAMESPACE}/pods/{name}/resize",
            {"spec": {"containers": [{"name": container, "resources": resources}]}},
            "application/strategic-merge-patch+json",
        )

    def require_console_pod(self, name):
        pod = self.pod(name)
        labels = pod["metadata"].get("labels", {})
        if labels.get("app") != CONSUMER_APP:
            raise ScopeError(f"{name} is not a console consumer pod")
        return pod

    def apply_cpu_hpa(self, body):
        _require_name(body, CPU_HPA)
        return self._replace(
            f"/apis/autoscaling/v2/namespaces/{NAMESPACE}/horizontalpodautoscalers", CPU_HPA, body
        )

    def delete_cpu_hpa(self):
        return self._delete(
            f"/apis/autoscaling/v2/namespaces/{NAMESPACE}/horizontalpodautoscalers/{CPU_HPA}"
        )

    def apply_scaled_object(self, body):
        _require_name(body, SCALED_OBJECT)
        return self._replace(
            f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects", SCALED_OBJECT, body
        )

    def delete_scaled_object(self):
        return self._delete(
            f"/apis/keda.sh/v1alpha1/namespaces/{NAMESPACE}/scaledobjects/{SCALED_OBJECT}"
        )

    def _replace(self, collection, name, body):
        if body["spec"]["scaleTargetRef"]["name"] != CONSUMER:
            raise ScopeError("Autoscalers may only target the console consumer Deployment")
        try:
            current = self.request("GET", f"{collection}/{name}")
        except KubeError as error:
            if error.status != 404:
                raise
            return self.request("POST", collection, body)
        body = json.loads(json.dumps(body))
        body["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
        return self.request("PUT", f"{collection}/{name}", body)

    def _delete(self, path):
        try:
            return self.request("DELETE", path)
        except KubeError as error:
            if error.status == 404:
                return None
            raise


def _name(name):
    if not isinstance(name, str) or not name or len(name) > 253:
        raise ScopeError("Resource names must be nonempty strings")
    if any(not (char.isalnum() or char in "-.") for char in name) or name != name.lower():
        raise ScopeError("Resource names must be lowercase DNS names")
    return name


def _infrastructure(role):
    if role not in INFRASTRUCTURE:
        raise ScopeError("Only the StreamScale Kafka and PostgreSQL StatefulSets are permitted")
    return INFRASTRUCTURE[role]


def _require_name(body, name):
    if body.get("metadata", {}).get("name") != name:
        raise ScopeError(f"Only the {name} resource may be written")
    if body["metadata"].get("namespace", NAMESPACE) != NAMESPACE:
        raise ScopeError("Only the streamscale namespace is permitted")


def _millicore_text(value):
    if not isinstance(value, str) or not value.endswith("m") or not value[:-1].isdigit():
        raise ScopeError("CPU quantities must be expressed in millicores, for example 100m")
    if not 20 <= int(value[:-1]) <= 1000:
        raise ScopeError("CPU quantities must stay between 20m and 1000m")
    return value


def _millicores(quantity):
    if quantity.endswith("m"):
        return int(quantity[:-1])
    return int(float(quantity) * 1000)


def cpu_cores(quantity):
    """Kubernetes CPU quantity (n, u, m or cores) to cores."""
    for suffix, scale in (("n", 1e-9), ("u", 1e-6), ("m", 1e-3)):
        if quantity.endswith(suffix):
            return float(quantity[: -len(suffix)]) * scale
    return float(quantity)


def memory_bytes(quantity):
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "k": 1e3, "M": 1e6, "G": 1e9}
    for suffix, scale in units.items():
        if quantity.endswith(suffix):
            return float(quantity[: -len(suffix)]) * scale
    return float(quantity)
