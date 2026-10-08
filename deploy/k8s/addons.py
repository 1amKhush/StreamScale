"""Pinned autoscaling dependencies, installed only in kind-streamscale."""

import hashlib
import urllib.request

from manage import ROOT, TOOLS, call, kubectl

METRICS_SHA256 = "1cec29a5267809306a2c6ec74a3e449abbb705b4a8beed0c8a1963910f72c79b"
KEDA_VERSION = "2.21.0"
KEDA_SHA256 = "a7da56bf41a33dad9d2fc82a6fdecee552fff947d80bf1e1dbcf4db0f9e26dee"
METRICS_IMAGE = (
    "registry.k8s.io/metrics-server/metrics-server:v0.9.0"
    "@sha256:d9862115e7c7881280d3d75ca26bda8ffc0fc213315979575bf23ce9826205c0"
)
KEDA_DIGESTS = {
    "keda": "sha256:81fe6547ce8d1cc29273f887b76507e9b853e5bc1f875ec23fe60061a38ed809",
    "metricsApiServer": "sha256:255375037fe592732eeb554743aae360c2da88cf0b9b1258d0a987ef885f0881",
    "webhooks": "sha256:e1969628cca6123e32eea13c47c75817091af2657fa68d9d0d58a8620d1b75b1",
}


def main():
    manifest = ROOT / "deploy/k8s/addons/metrics-server.yaml"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != METRICS_SHA256:
        raise RuntimeError("Vendored official Metrics Server manifest checksum changed")
    # The manifest contains cluster RBAC; kubectl always specifies our dedicated context.
    configured = manifest.read_text().replace(
        "--metric-resolution=15s",
        "--metric-resolution=10s\n        - --kubelet-request-timeout=5s\n"
        "        - --kubelet-insecure-tls",
    )
    configured = configured.replace(
        "registry.k8s.io/metrics-server/metrics-server:v0.9.0", METRICS_IMAGE
    )
    kubectl("apply", "-f", "-", "-n", "kube-system", input=configured)
    kubectl("rollout", "status", "deployment/metrics-server", "-n", "kube-system", "--timeout=300s")
    kubectl(
        "wait", "--for=condition=Available", "apiservice/v1beta1.metrics.k8s.io", "--timeout=120s"
    )
    chart = ROOT / ".tools/charts" / f"keda-{KEDA_VERSION}.tgz"
    chart.parent.mkdir(parents=True, exist_ok=True)
    if not chart.exists():
        url = f"https://kedacore.github.io/charts/keda-{KEDA_VERSION}.tgz"
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != KEDA_SHA256:
            raise RuntimeError("Official KEDA chart checksum mismatch")
        chart.write_bytes(payload)
    if hashlib.sha256(chart.read_bytes()).hexdigest() != KEDA_SHA256:
        raise RuntimeError("Cached KEDA chart checksum mismatch")
    arguments = [
        TOOLS / "helm",
        "upgrade",
        "--install",
        "streamscale-keda",
        chart,
        "--namespace",
        "streamscale-keda",
        "--create-namespace",
        "--kube-context",
        "kind-streamscale",
        "--set",
        "watchNamespace=streamscale",
        "--set",
        "image.pullPolicy=IfNotPresent",
        "--server-side=false",
        "--wait",
        "--timeout",
        "600s",
    ]
    for role, digest in KEDA_DIGESTS.items():
        arguments.extend(["--set-string", f"image.{role}.tag={KEDA_VERSION}@{digest}"])
    call(arguments)
    kubectl("wait", "--for=condition=Established", "crd/scaledobjects.keda.sh", "--timeout=120s")
    kubectl(
        "wait",
        "--for=condition=Available",
        "apiservice/v1beta1.external.metrics.k8s.io",
        "--timeout=120s",
    )
    print("Metrics Server 0.9.0 and KEDA 2.21.0 are ready in kind-streamscale.")


if __name__ == "__main__":
    main()
