"""Host-side, project-scoped cluster access for the console.

Every call uses the project's kubeconfig and the kind-streamscale context, never
the user's default configuration, and names only StreamScale console resources.
"""

import base64
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / ".tools/bin"
KUBECONFIG = ROOT / "artifacts/kubeconfig"
CONTEXT = "kind-streamscale"
NAMESPACE = "streamscale"
CLUSTER = "streamscale"
RELEASE = "streamscale-console"
TOKEN_SECRET = "streamscale-console-token"
AGENT_SERVICE = "service/streamscale-console-agent"
ARTIFACTS = ROOT / "artifacts/console"


class ClusterError(RuntimeError):
    pass


def environment():
    return os.environ | {
        "KUBECONFIG": str(KUBECONFIG),
        "HELM_CACHE_HOME": str(ROOT / ".tools/helm/cache"),
        "HELM_CONFIG_HOME": str(ROOT / ".tools/helm/config"),
        "HELM_DATA_HOME": str(ROOT / ".tools/helm/data"),
    }


def run(arguments, *, check=True, input=None, timeout=120):
    try:
        result = subprocess.run(
            [str(argument) for argument in arguments],
            cwd=ROOT,
            env=environment(),
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ClusterError(f"{Path(str(arguments[0])).name} timed out") from None
    if check and result.returncode:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise ClusterError(message[-1][:300] if message else "command failed")
    return result


def kubectl(*arguments, **kwargs):
    if not (TOOLS / "kubectl").exists() or not KUBECONFIG.exists():
        raise ClusterError("Cluster tools/config are missing; run ./streamscale.sh cluster")
    return run(
        [TOOLS / "kubectl", "--context", CONTEXT, "--namespace", NAMESPACE, *arguments], **kwargs
    )


def helm(*arguments, **kwargs):
    return run(
        [TOOLS / "helm", *arguments, "--namespace", NAMESPACE, "--kube-context", CONTEXT], **kwargs
    )


def node_running():
    result = run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=io.x-k8s.kind.cluster={CLUSTER}",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        check=False,
    )
    return bool(result.stdout.strip())


def ensure_token():
    existing = kubectl("get", "secret", TOKEN_SECRET, "-o", "name", check=False)
    if existing.returncode == 0:
        return
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": TOKEN_SECRET, "namespace": NAMESPACE},
        "stringData": {"token": secrets.token_urlsafe(48)},
    }
    kubectl("create", "-f", "-", input=json.dumps(body))


def agent_token():
    secret = json.loads(kubectl("get", "secret", TOKEN_SECRET, "-o", "json").stdout)
    return base64.b64decode(secret["data"]["token"]).decode()


def unused_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class AgentForward:
    """A supervised loopback port forward to the in-cluster agent service."""

    def __init__(self, log_directory=ARTIFACTS):
        self.process = None
        self.port = None
        self.lock = threading.Lock()
        self.log_directory = log_directory
        self.starts = 0

    @property
    def url(self):
        return None if self.port is None else f"http://127.0.0.1:{self.port}"

    def alive(self):
        return self.process is not None and self.process.poll() is None

    @property
    def restarts(self):
        """Forwards re-established after the first, whatever ended the previous one."""
        return max(0, self.starts - 1)

    def start(self, timeout=20):
        with self.lock:
            self._stop()
            self.starts += 1
            self.port = unused_port()
            self.log_directory.mkdir(parents=True, exist_ok=True)
            log = (self.log_directory / "agent-forward.log").open("a")
            self.process = subprocess.Popen(
                [
                    str(TOOLS / "kubectl"),
                    "--context",
                    CONTEXT,
                    "--namespace",
                    NAMESPACE,
                    "port-forward",
                    AGENT_SERVICE,
                    f"{self.port}:9200",
                    "--address",
                    "127.0.0.1",
                ],
                cwd=ROOT,
                env=environment(),
                stdout=log,
                stderr=log,
            )
            log.close()
            deadline = time.monotonic() + timeout
            while True:
                if self.process.poll() is not None:
                    raise ClusterError("Agent port forward exited; see artifacts/console")
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                        return self.url
                except OSError:
                    if time.monotonic() >= deadline:
                        self._stop()
                        raise ClusterError("Agent port forward did not become ready") from None
                    time.sleep(0.1)

    def restart(self):
        return self.start()

    def _stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None

    def stop(self):
        with self.lock:
            self._stop()
