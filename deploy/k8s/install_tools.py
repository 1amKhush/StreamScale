"""Download version-pinned official tools; verify publisher SHA256 before execution."""

import hashlib
import io
import platform
import tarfile
import urllib.request
from pathlib import Path

DESTINATION = Path(".tools/bin")


def download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def main():
    system = platform.system().lower()
    architecture = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if system not in {"linux", "darwin"} or architecture is None:
        raise SystemExit("Installer supports Linux/macOS on amd64/arm64")
    platform_name = f"{system}-{architecture}"
    urls = {
        "kind": (
            f"https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-{platform_name}",
            ".sha256sum",
        ),
        "kubectl": (
            f"https://dl.k8s.io/release/v1.37.0/bin/{system}/{architecture}/kubectl",
            ".sha256",
        ),
        "helm": (f"https://get.helm.sh/helm-v4.3.0-{platform_name}.tar.gz", ".sha256sum"),
    }
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, (url, checksum_suffix) in urls.items():
        payload = download(url)
        checksum = download(url + checksum_suffix).decode().split()[0]
        if hashlib.sha256(payload).hexdigest() != checksum:
            raise SystemExit(f"Official checksum mismatch for {name}")
        if name == "helm":
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                member = archive.extractfile(f"{platform_name}/helm")
                if member is None:
                    raise SystemExit("Helm binary not found in release archive")
                payload = member.read()
        target = DESTINATION / name
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.chmod(0o755)
        temporary.replace(target)
        print(f"Installed {target} (official SHA256 verified)", flush=True)


if __name__ == "__main__":
    main()
