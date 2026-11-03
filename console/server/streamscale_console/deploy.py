"""Build the console worker image on the deployed StreamScale image and install it."""

import hashlib
import importlib.util
import json
from datetime import UTC, datetime

from streamscale_console.cluster import (
    ARTIFACTS,
    CLUSTER,
    RELEASE,
    ROOT,
    TOOLS,
    ClusterError,
    ensure_token,
    helm,
    kubectl,
    node_running,
    run,
)

CONSOLE = ROOT / "console"
SOURCES = ("worker/streamscale_console_worker", "deploy/worker.Dockerfile", "deploy/chart")


def console_digest():
    digest = hashlib.sha256()
    for name in SOURCES:
        path = CONSOLE / name
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for item in files:
            if "__pycache__" in item.parts:
                continue
            digest.update(str(item.relative_to(CONSOLE)).encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def pipeline_image():
    """Build the current pipeline base without replacing the retained measured run."""
    result = kubectl(
        "get",
        "deployment",
        "streamscale-consumer",
        "-o",
        "jsonpath={.spec.template.spec.containers[0].image}",
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise ClusterError("The StreamScale pipeline is not deployed; run ./streamscale.sh cluster")
    image = result.stdout.strip()
    inspected = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], check=False)
    if inspected.returncode:
        raise ClusterError(
            f"Deployed image {image} is not available locally; rerun ./streamscale.sh cluster"
        )
    # Build the current consumer source without redeploying or replacing a retained
    # measured run. All console modes use this same base image and resource limits.
    spec = importlib.util.spec_from_file_location(
        "streamscale_image_builder", ROOT / "deploy/k8s/manage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = module.build()
    return metadata["image"], metadata["image_id"]


def loaded(image):
    node = f"{CLUSTER}-control-plane"
    result = run(
        ["docker", "exec", node, "crictl", "inspecti", "docker.io/library/" + image], check=False
    )
    return result.returncode == 0


def deploy(wait=True):
    if not node_running():
        raise ClusterError("The kind node is stopped; run ./streamscale.sh resume")
    base, base_id = pipeline_image()
    source = console_digest()
    tag = hashlib.sha256((base_id + source).encode()).hexdigest()[:16]
    image = f"streamscale-console:{tag}"
    if run(["docker", "image", "inspect", image], check=False).returncode:
        run(
            [
                "docker",
                "build",
                "--file",
                CONSOLE / "deploy/worker.Dockerfile",
                "--build-arg",
                f"BASE={base}",
                "--build-arg",
                f"CONSOLE_SOURCE_SHA256={source}",
                "--tag",
                image,
                CONSOLE / "worker",
            ],
            timeout=600,
        )
    if not loaded(image):
        run([TOOLS / "kind", "load", "docker-image", image, "--name", CLUSTER], timeout=600)
    ensure_token()
    arguments = [
        "upgrade",
        "--install",
        RELEASE,
        CONSOLE / "deploy/chart",
        "--set-string",
        f"image.tag={tag}",
        "--timeout",
        "5m",
    ]
    if wait:
        arguments.append("--wait")
    helm(*arguments, timeout=420)
    record = {
        "deployed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "release": RELEASE,
        "image": image,
        "base_image": base,
        "base_image_id": base_id,
        "console_source_sha256": source,
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "deployment.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def installed():
    return helm("status", RELEASE, "-o", "json", check=False).returncode == 0


def main():
    record = deploy()
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
