"""Poll the in-cluster agent, derive chart points, and fan them out to browsers.

The hub owns the agent port forward. When the agent stops answering, the hub
marks the connection unreachable (never zeroing values), restarts the forward
with backoff, and backfills missed samples from the agent's bounded history.
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections import deque

from streamscale_console.cluster import AgentForward, ClusterError, agent_token
from streamscale_console.derive import Deriver


class AgentUnavailable(RuntimeError):
    pass


class AgentRejected(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class AgentClient:
    def __init__(self, forward: AgentForward, token_provider=agent_token):
        self.forward = forward
        self.token_provider = token_provider
        self.token = None

    def request(self, method, path, body=None, timeout=10):
        if self.forward.url is None or not self.forward.alive():
            raise AgentUnavailable("Agent port forward is not running")
        if self.token is None:
            self.token = self.token_provider()
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.forward.url + path,
            data=data,
            method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get("error", error.reason)
            except (json.JSONDecodeError, AttributeError):
                message = str(error.reason)
            if error.code == 401:
                self.token = None
            raise AgentRejected(error.code, message) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise AgentUnavailable(type(error).__name__) from None

    async def call(self, method, path, body=None, timeout=10):
        return await asyncio.to_thread(self.request, method, path, body, timeout)


class TelemetryHub:
    def __init__(self, forward=None, client=None, interval=1.0):
        self.forward = forward or AgentForward()
        self.client = client or AgentClient(self.forward)
        self.interval = interval
        self.deriver = Deriver()
        self.points = deque(maxlen=7200)
        self.latest = None
        self.agent = None
        self.sequence = None
        self.listeners = []
        self.sample_listeners = []
        self.status = {"agent": "connecting", "since": time.time(), "error": None, "last_ok": None}

    def set_status(self, state, error=None):
        changed = state != self.status["agent"] or error != self.status["error"]
        if state != self.status["agent"]:
            self.status["since"] = time.time()
        self.status.update(agent=state, error=error)
        if state == "connected":
            self.status["last_ok"] = time.time()
        if changed:
            self.broadcast({"type": "status", "status": self.public_status()})

    def public_status(self):
        return self.status | {"forward_restarts": self.forward.restarts}

    def broadcast(self, message):
        for listener in list(self.listeners):
            listener(message)

    def ingest(self, payload):
        self.agent = payload.get("agent")
        received = time.time()
        samples = [
            sample
            for sample in payload.get("samples", [])
            if self.sequence is None or sample["sequence"] > self.sequence
        ]
        for index, sample in enumerate(samples):
            self.sequence = sample["sequence"]
            point = self.deriver.point(sample, received)
            self.points.append(point)
            self.latest = sample
            # Backfilled history feeds charts only; lifecycle decisions use live samples.
            live = len(samples) <= 3 or index == len(samples) - 1
            for listener in self.sample_listeners:
                listener(sample, point, live)
            self.broadcast({"type": "tick", "point": point, "sample": compact(sample)})

    async def poll_once(self):
        # First contact backfills the agent's bounded history, so a restarted console
        # shows the minutes it missed instead of starting blank.
        after = 0 if self.sequence is None else self.sequence
        path = f"/v1/telemetry?after={after}"
        payload = await self.client.call("GET", path, timeout=5)
        if self.sequence is not None and not payload.get("samples"):
            # Either nothing new yet, or the agent restarted with a lower sequence.
            latest = await self.client.call("GET", "/v1/telemetry", timeout=5)
            newest = latest["samples"][0]["sequence"] if latest.get("samples") else None
            if newest is not None and newest < self.sequence:
                self.sequence = None
                self.deriver = Deriver()
                payload = latest
        self.ingest(payload)

    async def run(self):
        failures = 0
        while True:
            started = time.monotonic()
            try:
                if not self.forward.alive():
                    await asyncio.to_thread(self.forward.start)
                await self.poll_once()
                failures = 0
                self.set_status("connected")
            except (AgentUnavailable, AgentRejected, ClusterError) as error:
                failures += 1
                self.set_status("unreachable", str(error)[:200])
                if failures >= 3 or not self.forward.alive():
                    try:
                        await asyncio.to_thread(self.forward.restart)
                    except ClusterError as restart_error:
                        self.set_status("unreachable", str(restart_error)[:200])
                await asyncio.sleep(min(2 * failures, 10))
                continue
            await asyncio.sleep(max(0.05, self.interval - (time.monotonic() - started)))


def compact(sample):
    """The latest sample for inspectors, minus bulky histogram buckets."""
    result = json.loads(json.dumps(sample))
    for row in result.get("consumers") or []:
        metrics = row.get("metrics")
        if metrics:
            for kind in ("e2e", "db"):
                if kind in metrics:
                    metrics[kind] = {
                        "count": metrics[kind]["count"],
                        "sum": metrics[kind]["sum"],
                    }
    return result
