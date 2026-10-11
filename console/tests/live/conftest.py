"""Live console fixtures: a real console server against the deployed kind release."""

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import pytest
from streamscale_console.cluster import ARTIFACTS, ROOT, kubectl


class ConsoleClient:
    def __init__(self, record):
        self.url = record["url"]
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.open(record["login_url"], timeout=10).read()

    def get(self, path, timeout=15):
        with self.opener.open(self.url + path, timeout=timeout) as response:
            return json.loads(response.read())

    def state(self):
        return self.get("/api/state")

    def submit(self, kind, params=None, key=None):
        body = {"kind": kind, "params": params or {}, "key": key or "test-" + uuid.uuid4().hex}
        request = urllib.request.Request(
            self.url + "/api/operations",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": self.url},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return {"error": json.loads(error.read()).get("error"), "status": error.code}

    def run(self, kind, params=None, timeout=240):
        """Submit an operation and wait for its terminal state."""
        submitted = self.submit(kind, params)
        if "error" in submitted:
            return submitted
        identifier = submitted["operation"]["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            operation = self.get(f"/api/operations/{identifier}")
            if operation["state"] not in {"queued", "running"}:
                return operation
            time.sleep(0.5)
        raise AssertionError(f"{kind} did not finish within {timeout}s")

    def wait(self, predicate, timeout=120, interval=1.0, describe="condition"):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.state()
            try:
                if predicate(last):
                    return last
            except (KeyError, TypeError, IndexError):
                pass
            time.sleep(interval)
        raise AssertionError(f"Timed out waiting for {describe}")


@pytest.fixture(scope="session")
def console_server():
    """Start a console server on an available port for the whole live session."""
    record_path = ARTIFACTS / "server.json"
    log = (ARTIFACTS / "live-test-server.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "streamscale_console", "serve", "--port", "0"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ | {"PYTHONUNBUFFERED": "1"},
    )
    deadline = time.monotonic() + 60
    record = None
    while record is None:
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Console server did not start; see artifacts/console")
        try:
            found = json.loads(record_path.read_text())
            record = found if found.get("pid") == process.pid else None
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    while True:
        try:
            urllib.request.urlopen(record["url"] + "/health", timeout=2).read()
            break
        except (urllib.error.URLError, ConnectionError):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Console server did not accept connections") from None
            time.sleep(0.2)
    client = ConsoleClient(record)
    client.wait(lambda state: state["status"]["agent"] == "connected" and state["points"], 90)
    yield client, record
    process.terminate()
    try:
        process.wait(20)
    except subprocess.TimeoutExpired:
        process.kill()
    log.close()


@pytest.fixture
def console(console_server):
    return console_server[0]


def kube_json(*arguments):
    return json.loads(kubectl(*arguments, "-o", "json").stdout)


def wait_idle(console, timeout=480):
    """Let any earlier sale finish draining so a test starts from an empty floor."""
    return console.wait(
        lambda state: (
            state["sales"]["session"] is None
            and (state["sample"].get("sale") or {}).get("state")
            in {None, "idle", "finished", "failed"}
        ),
        timeout=timeout,
        describe="the previous sale to finish draining",
    )
