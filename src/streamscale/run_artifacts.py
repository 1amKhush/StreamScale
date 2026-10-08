"""Small per-run files shared by the offline workload tool and consumer commands."""

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from confluent_kafka import Consumer, TopicPartition

from streamscale.topics import PARTITIONS


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def write_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_run(directory: Path) -> dict:
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    if run.get("artifact_version") != 1:
        raise ValueError("Unsupported run artifact version")
    return run


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def manifest_hash(attempts: list[dict], logical: bool = False) -> str:
    fields = [
        {key: value for key, value in attempt.items() if not logical or key != "event_id"}
        for attempt in attempts
    ]
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_manifest(directory: Path, run: dict) -> list[dict]:
    attempts = read_jsonl(directory / "manifest.jsonl")
    if manifest_hash(attempts) != run["manifest_sha256"]:
        raise RuntimeError("Workload manifest checksum mismatch")
    return attempts


def read_acknowledgements(directory: Path, run: dict) -> list[dict]:
    path = directory / "acknowledgements.jsonl"
    if hashlib.sha256(path.read_bytes()).hexdigest() != run["acknowledgements_sha256"]:
        raise RuntimeError("Acknowledgement log checksum mismatch")
    return read_jsonl(path)


def offsets(bootstrap: str, topic: str) -> dict[str, int]:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "streamscale.offset-probe",
            "enable.auto.commit": False,
        }
    )
    try:
        return {
            str(partition): consumer.get_watermark_offsets(
                TopicPartition(topic, partition), timeout=10, cached=False
            )[1]
            for partition in range(PARTITIONS)
        }
    finally:
        consumer.close()


def revision_snapshot() -> dict:
    if os.environ.get("STREAMSCALE_GIT_COMMIT"):
        # Build-time metadata is baked into the image; containers need no Git checkout.
        return {
            "git_commit": os.environ["STREAMSCALE_GIT_COMMIT"],
            "git_dirty": os.environ.get("STREAMSCALE_GIT_DIRTY") == "true",
            "source_sha256": os.environ["STREAMSCALE_SOURCE_SHA256"],
            "image": os.environ["STREAMSCALE_IMAGE"],
        }
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    if revision.returncode:
        raise RuntimeError("Run publishing from the repository root to capture its Git revision")
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    digest = hashlib.sha256()
    files = [Path("pyproject.toml"), Path("uv.lock")]
    for directory in ("src", "tools/workload", "schemas", "db/migrations"):
        files.extend(
            path for path in Path(directory).rglob("*") if path.suffix in {".py", ".json", ".sql"}
        )
    for path in sorted(files):
        digest.update(str(path).encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "git_commit": revision.stdout.strip(),
        "git_dirty": bool(dirty.stdout),
        "source_sha256": digest.hexdigest(),
    }
