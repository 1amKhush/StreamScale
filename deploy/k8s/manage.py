"""Project-scoped Docker/kind/Helm commands; never use the user's default kubeconfig."""

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from streamscale.run_artifacts import revision_snapshot, write_json

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/k8s"
CLUSTER = "streamscale"
NAMESPACE = "streamscale"
SECRET = "streamscale-local-credentials"
TOOLS = ROOT / ".tools/bin"


def call(arguments, *, capture=False, input=None, check=True):
    return subprocess.run(
        [str(arg) for arg in arguments],
        cwd=ROOT,
        env=os.environ
        | {
            "KUBECONFIG": str(ROOT / "artifacts/kubeconfig"),
        },
        input=input,
        text=True,
        capture_output=capture,
        check=check,
    )


def kubectl(*arguments, **kwargs):
    return call(
        [TOOLS / "kubectl", "--context", f"kind-{CLUSTER}", "-n", NAMESPACE, *arguments], **kwargs
    )


def build():
    metadata = revision_snapshot()
    tag = hashlib.sha256(
        (metadata["source_sha256"] + (ROOT / "Dockerfile").read_text()).encode()
    ).hexdigest()[:16]
    image = f"streamscale:{tag}"
    call(
        [
            "docker",
            "build",
            "--tag",
            image,
            "--build-arg",
            f"GIT_COMMIT={metadata['git_commit']}",
            "--build-arg",
            f"GIT_DIRTY={str(metadata['git_dirty']).lower()}",
            "--build-arg",
            f"SOURCE_SHA256={metadata['source_sha256']}",
            "--build-arg",
            f"IMAGE_REF={image}",
            ".",
        ]
    )
    metadata["image"] = image
    metadata["image_id"] = call(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture=True
    ).stdout.strip()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / "image.json", metadata)
    return metadata


def cluster():
    names = call([TOOLS / "kind", "get", "clusters"], capture=True).stdout.splitlines()
    if CLUSTER not in names:
        call(
            [
                TOOLS / "kind",
                "create",
                "cluster",
                "--name",
                CLUSTER,
                "--config",
                "deploy/k8s/kind.yaml",
                "--kubeconfig",
                "artifacts/kubeconfig",
                "--wait",
                "180s",
            ]
        )
    else:
        call(
            [
                TOOLS / "kind",
                "export",
                "kubeconfig",
                "--name",
                CLUSTER,
                "--kubeconfig",
                "artifacts/kubeconfig",
            ]
        )


def credentials():
    existing = kubectl("get", "secret", SECRET, "-o", "json", capture=True, check=False)
    if existing.returncode == 0:
        credential = json.loads(existing.stdout)
        values = {
            key: base64.b64decode(value).decode() for key, value in credential["data"].items()
        }
    else:
        password = secrets.token_urlsafe(24)
        values = {
            "POSTGRES_PASSWORD": password,
            "DATABASE_URL": f"postgresql://streamscale:{password}@streamscale-postgres:5432/streamscale",
            "GF_SECURITY_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        }
        kubectl(
            "create",
            "-f",
            "-",
            input=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {"name": SECRET, "namespace": NAMESPACE},
                    "stringData": values,
                }
            ),
        )
    path = OUTPUT / "credentials.json"
    # Private file, never print passwords or embed them in Helm values/history.
    with open(
        path, "w", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)
    ) as out:
        json.dump(
            {"grafana_user": "admin", "grafana_password": values["GF_SECURITY_ADMIN_PASSWORD"]},
            out,
            indent=2,
        )
    path.chmod(0o600)
    print(f"Grafana login saved in {path.relative_to(ROOT)}")


def up(args):
    metadata = build()
    cluster()
    call([TOOLS / "kind", "load", "docker-image", metadata["image"], "--name", CLUSTER])
    if kubectl("get", "namespace", NAMESPACE, capture=True, check=False).returncode:
        kubectl("create", "namespace", NAMESPACE)
    credentials()
    name = "run-" + datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]
    info = metadata | {"run_name": name, "namespace": NAMESPACE, "context": f"kind-{CLUSTER}"}
    write_json(OUTPUT / "deployment.json", info)
    call(
        [
            TOOLS / "helm",
            "upgrade",
            "--install",
            "streamscale",
            "deploy/k8s/chart",
            "--namespace",
            NAMESPACE,
            "--kube-context",
            f"kind-{CLUSTER}",
            "--set-string",
            f"image.tag={metadata['image'].split(':')[1]}",
            "--set-string",
            f"run.name={name}",
            "--set",
            f"run.orders={args.orders}",
            "--set-string",
            f"run.profile={args.profile}",
            "--wait",
            "--wait-for-jobs",
            "--timeout",
            "10m",
        ]
    )
    print(f"Cluster run ready: /runs/{name}; use make k8s-verify and make k8s-ui")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "up", "status", "logs", "credentials"))
    parser.add_argument("--orders", type=int, default=200)
    parser.add_argument(
        "--profile", choices=("normal", "correctness", "fixtures"), default="correctness"
    )
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.command == "build":
        build()
    elif args.command == "up":
        up(args)
    elif args.command == "credentials":
        credentials()
    elif args.command == "logs":
        kubectl("logs", "deployment/streamscale-consumer", "--tail=80")
    else:
        kubectl("get", "pods,jobs,svc,pvc")


if __name__ == "__main__":
    main()
