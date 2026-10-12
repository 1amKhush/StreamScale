"""M15 lifecycle: the maintained launcher's console command, its links and its shutdown.

Runs before the shared live-session server starts (file order), because only one
console server may run at a time.
"""

import http.cookiejar
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from streamscale_console.cluster import ARTIFACTS, ROOT

pytestmark = pytest.mark.live


def listening(url):
    host, port = url.split("//", 1)[1].split("/", 1)[0].split(":")
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, int(port))) == 0


def test_launcher_console_serves_links_and_stops_cleanly():
    process = subprocess.Popen(
        [
            "./streamscale.sh",
            "console",
            "--port",
            "0",
            "--grafana-port",
            "0",
            "--prometheus-port",
            "0",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ | {"PYTHONUNBUFFERED": "1"},
    )
    lines = []
    urls = {}
    deadline = time.monotonic() + 600
    try:
        while (
            time.monotonic() < deadline and not {"console", "grafana", "prometheus"} <= urls.keys()
        ):
            line = process.stdout.readline()
            if not line:
                assert process.poll() is None, "".join(lines[-20:])
                continue
            lines.append(line)
            for name, prefix in (
                ("console", "StreamScale console: "),
                ("grafana", "Grafana: "),
                ("prometheus", "Prometheus: "),
            ):
                if line.startswith(prefix):
                    urls[name] = line[len(prefix) :].strip()
        assert {"console", "grafana", "prometheus"} <= urls.keys(), "".join(lines[-20:])
        login = urls["console"]
        base = login.split("/?token=")[0]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        deadline = time.monotonic() + 60
        while True:
            try:
                opener.open(login, timeout=5).read()
                break
            except (urllib.error.URLError, ConnectionError):
                assert time.monotonic() < deadline
                time.sleep(0.5)
        while True:
            state = json.loads(opener.open(base + "/api/state", timeout=10).read())
            if state["status"]["agent"] == "connected":
                break
            assert time.monotonic() < deadline
            time.sleep(1)
        assert state["server"]["dashboards"]["grafana"] == urls["grafana"]
        grafana = urls["grafana"].removesuffix("/d/streamscale")
        assert (
            json.loads(urllib.request.urlopen(grafana + "/api/health", timeout=10).read())[
                "database"
            ]
            == "ok"
        )
        assert urllib.request.urlopen(urls["prometheus"] + "/-/ready", timeout=10).status == 200

        # Only one console server may run: a second one is refused.
        second = subprocess.run(
            [sys.executable, "-m", "streamscale_console", "serve", "--port", "0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert second.returncode != 0 and "Another console server is running" in second.stderr

        status = subprocess.run(
            ["./streamscale.sh", "status"], cwd=ROOT, capture_output=True, text=True, timeout=120
        )
        assert "streamscale-console-agent" in status.stdout
        assert "streamscale-console-consumer" in status.stdout
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(60)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
    # Ctrl+C closed the console and the forwards it opened, and recorded the stop.
    # uv returns once it has passed the signal on, so allow the server to finish.
    deadline = time.monotonic() + 30
    while True:
        journal = (ARTIFACTS / "journal.jsonl").read_text().splitlines()[-10:]
        stopped = any(json.loads(line).get("text") == "Console server stopped" for line in journal)
        done = stopped and not (ARTIFACTS / "server.json").exists()
        if done and not any(listening(url) for url in urls.values()):
            break
        assert time.monotonic() < deadline, "the console did not shut down cleanly"
        time.sleep(0.5)
