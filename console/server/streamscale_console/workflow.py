"""Coordinate with CLI and experiment workflows through the existing cluster lock.

`deploy/k8s/manage.py` holds an exclusive flock on artifacts/k8s/workflow.lock
for cluster deployments, scaling exercises and measured campaigns. The console
holds the same lock while a sale is active, so neither side can publish into the
other's captured source range. Status checks read /proc/locks and never take the
lock, so polling cannot make a starting experiment fail.
"""

import fcntl
import json
import os
from pathlib import Path

from streamscale_console.cluster import ROOT

LOCK = ROOT / "artifacts/k8s/workflow.lock"


class WorkflowBusy(RuntimeError):
    pass


def holder(lock=LOCK, locks=Path("/proc/locks")):
    """Describe the process holding the workflow lock, or None when it is free."""
    try:
        stat = lock.stat()
        table = locks.read_text()
    except FileNotFoundError:
        return None
    for line in table.splitlines():
        fields = line.split()
        if len(fields) < 6 or "->" in fields or fields[1] != "FLOCK":
            continue
        try:
            major, minor, inode = fields[5].split(":")
            same = int(inode) == stat.st_ino and (int(major, 16), int(minor, 16)) == (
                os.major(stat.st_dev),
                os.minor(stat.st_dev),
            )
        except ValueError:
            continue
        if not same:
            continue
        pid = int(fields[4])
        try:
            details = json.loads(lock.read_text() or "{}")
        except json.JSONDecodeError:
            details = {}
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            command = None
        return {
            "pid": pid,
            "self": pid == os.getpid(),
            "started_at": details.get("started_at"),
            "purpose": details.get("purpose"),
            "command": (command or "").strip()[:200] or None,
        }
    return None


class WorkflowLock:
    def __init__(self, lock=LOCK):
        self.path = lock
        self.handle = None
        self.purpose = None

    @property
    def held(self):
        return self.handle is not None

    def acquire(self, purpose):
        if self.handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            other = holder(self.path) or {}
            raise WorkflowBusy(
                "Another StreamScale workflow is using the cluster"
                + (f" (pid {other['pid']})" if other.get("pid") else "")
                + "; wait for it to finish"
            ) from None
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "started_at": _now(),
                "purpose": "interactive console: " + purpose,
            },
            handle,
        )
        handle.flush()
        self.handle = handle
        self.purpose = purpose

    def release(self):
        if self.handle is None:
            return
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None
        self.purpose = None


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
