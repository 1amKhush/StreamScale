"""Serialized, journaled console operations with duplicate-click protection.

Every state-changing request becomes one operation with an ID. Operations run one
at a time in submission order. A repeated idempotency key returns the original
operation instead of running it again, and each kind can refuse to start when
the current state makes it unsafe (for example, a second sale).
"""

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from streamscale_console.cluster import ARTIFACTS

KEY_RETENTION_SECONDS = 600


class Rejected(ValueError):
    """The operation is invalid in the current state; safe to show the user."""


@dataclass
class Operation:
    kind: str
    params: dict
    key: str
    id: str = field(default_factory=lambda: "op-" + uuid.uuid4().hex[:12])
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    result: dict | None = None
    progress: list = field(default_factory=list)
    session: str | None = None

    def public(self):
        return asdict(self)


@dataclass
class Kind:
    handler: object
    label: str
    check: object = None
    mutating: bool = True


class Journal:
    """Append-only action log for the workspace, and per session when one is active."""

    def __init__(self, directory=ARTIFACTS):
        self.directory = directory
        self.listeners = []
        self.tail = []

    def write(self, entry, session=None):
        entry = {"at": time.time()} | entry
        self.directory.mkdir(parents=True, exist_ok=True)
        targets = [self.directory / "journal.jsonl"]
        if session:
            targets.append(self.directory / "sessions" / session / "journal.jsonl")
        line = json.dumps(entry, default=str) + "\n"
        for path in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as output:
                output.write(line)
        self.tail = (self.tail + [entry])[-200:]
        for listener in self.listeners:
            listener({"type": "journal", "entry": entry})
        return entry

    def load_tail(self, limit=200):
        path = self.directory / "journal.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        self.tail = [json.loads(line) for line in lines if line.strip()]
        return self.tail


class Coordinator:
    def __init__(self, journal: Journal, session_provider=lambda: None):
        self.kinds: dict[str, Kind] = {}
        self.journal = journal
        self.session_provider = session_provider
        self.operations: dict[str, Operation] = {}
        self.keys: dict[str, tuple[float, str]] = {}
        self.queue: asyncio.Queue | None = None
        self.listeners = []
        self.current: Operation | None = None

    def register(self, name, handler, label, check=None, mutating=True):
        self.kinds[name] = Kind(handler, label, check, mutating)

    def emit(self, operation):
        message = {"type": "operation", "operation": operation.public()}
        for listener in self.listeners:
            listener(message)

    def submit(self, kind, params, key):
        if kind not in self.kinds:
            raise Rejected(f"Unknown operation: {kind}")
        if not isinstance(params, dict):
            raise Rejected("Operation parameters must be an object")
        if not isinstance(key, str) or not 8 <= len(key) <= 80:
            raise Rejected("Each request needs an idempotency key of 8–80 characters")
        now = time.time()
        self.keys = {k: v for k, v in self.keys.items() if now - v[0] < KEY_RETENTION_SECONDS}
        if key in self.keys:
            return self.operations[self.keys[key][1]], False
        definition = self.kinds[kind]
        pending = [
            op
            for op in self.operations.values()
            if op.kind == kind and op.state in {"queued", "running"} and op.params == params
        ]
        if pending:
            # The same request is already in flight under another key: do not repeat it.
            self.keys[key] = (now, pending[0].id)
            return pending[0], False
        if definition.check is not None:
            definition.check(params)
        operation = Operation(kind, params, key, session=self.session_provider())
        self.operations[operation.id] = operation
        self.keys[key] = (now, operation.id)
        self.journal.write(
            {
                "type": "operation",
                "kind": kind,
                "label": definition.label,
                "op_id": operation.id,
                "state": "queued",
                "params": params,
            },
            operation.session,
        )
        self.emit(operation)
        self.queue.put_nowait(operation.id)
        if len(self.operations) > 500:
            finished = [op for op in self.operations.values() if op.ended_at]
            for op in sorted(finished, key=lambda row: row.ended_at)[:100]:
                self.operations.pop(op.id, None)
        return operation, True

    def report(self, operation, text):
        operation.progress.append({"at": time.time(), "text": text})
        self.emit(operation)

    async def run(self):
        self.queue = asyncio.Queue()
        while True:
            identifier = await self.queue.get()
            operation = self.operations.get(identifier)
            if operation is None:
                continue
            definition = self.kinds[operation.kind]
            operation.state = "running"
            operation.started_at = time.time()
            self.current = operation
            self.emit(operation)
            try:
                if definition.check is not None:
                    definition.check(operation.params)
                operation.result = await definition.handler(operation)
                operation.state = "succeeded"
            except Rejected as error:
                operation.state = "rejected"
                operation.error = str(error)
            except Exception as error:  # noqa: BLE001 - reported to the user and journal
                operation.state = "failed"
                operation.error = str(error)[:400] or type(error).__name__
            operation.ended_at = time.time()
            self.current = None
            self.journal.write(
                {
                    "type": "operation",
                    "kind": operation.kind,
                    "label": definition.label,
                    "op_id": operation.id,
                    "state": operation.state,
                    "params": operation.params,
                    "error": operation.error,
                    "result": operation.result,
                    "duration_s": round(operation.ended_at - operation.started_at, 3),
                },
                operation.session,
            )
            self.emit(operation)

    def recent(self, limit=30):
        rows = sorted(self.operations.values(), key=lambda op: op.created_at)[-limit:]
        return [op.public() for op in rows]


def ensure_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    return path
