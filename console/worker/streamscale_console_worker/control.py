"""Shared files on the runs volume that connect the console agent and its consumers."""

import json
import os
import time
from pathlib import Path

from streamscale.run_artifacts import utc_now, write_json

ROOT = Path(os.environ.get("STREAMSCALE_CONSOLE_ROOT", "/runs/console"))


def current_path(root=ROOT):
    return root / "current.json"


def faults_path(root=ROOT):
    return root / "control" / "faults.json"


def claims_path(root=ROOT):
    return root / "control" / "claims"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def current_run(root=ROOT):
    """The console run consumers should follow, or None before the first sale."""
    pointer = read_json(current_path(root))
    if not pointer:
        return None
    directory = Path(pointer["run_dir"])
    if directory.parent != root / "runs":
        raise ValueError("Console run pointer escaped the console runs directory")
    return pointer


def set_current_run(run_name, root=ROOT):
    directory = root / "runs" / run_name
    if not (directory / "run.json").exists():
        raise RuntimeError("A console run must be generated before consumers follow it")
    write_json(
        current_path(root), {"run_name": run_name, "run_dir": str(directory), "set_at": utc_now()}
    )


def write_faults(faults, root=ROOT):
    path = faults_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"version": 1, "written_at": utc_now(), "faults": faults})


def claim(fault_id, detail, root=ROOT):
    """Atomically claim a one-shot fault; exactly one consumer process wins."""
    directory = claims_path(root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(directory / f"{fault_id}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(detail | {"claimed_at": utc_now(), "claimed_unix": time.time()}, output)
        output.flush()
        os.fsync(output.fileno())
    return True


def read_claim(fault_id, root=ROOT):
    return read_json(claims_path(root) / f"{fault_id}.json")
