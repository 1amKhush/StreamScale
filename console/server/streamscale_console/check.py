"""Complete console acceptance: unit, web, deployment, live cluster and browser checks.

Writes a dated summary to artifacts/console/check.json. Live stages use the
running kind cluster and the console release; they never stop project services.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from streamscale_console.cluster import ARTIFACTS, ROOT

WEB = ROOT / "console/web"


def now():
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def stage(name, arguments, results, cwd=ROOT, env=None):
    print(f"\n[console-check] {name}: {' '.join(str(a) for a in arguments)}", flush=True)
    started = time.monotonic()
    result = subprocess.run(arguments, cwd=cwd, env=os.environ | (env or {}))
    results.append(
        {
            "stage": name,
            "returncode": result.returncode,
            "seconds": round(time.monotonic() - started, 1),
        }
    )
    if result.returncode:
        raise SystemExit(f"Console check failed at {name}; see the output above")


def start_server():
    record = ARTIFACTS / "server.json"
    record.unlink(missing_ok=True)
    log = (ARTIFACTS / "check-server.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "streamscale_console", "serve", "--port", "0"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("Console server exited during startup; see artifacts/console")
        if record.exists():
            url = json.loads(record.read_text())["url"]
            try:
                urllib.request.urlopen(url + "/health", timeout=2).read()
                return process, log
            except (urllib.error.URLError, ConnectionError):
                pass
        time.sleep(0.3)
    process.terminate()
    raise SystemExit("Console server did not become ready")


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    started = now()
    results = []
    python = sys.executable
    stage("python unit tests", [python, "-m", "pytest", "console/tests", "-q"], results)
    stage("web type check", ["npm", "run", "typecheck"], results, cwd=WEB)
    stage("web unit tests", ["npm", "test"], results, cwd=WEB)
    stage("web build", ["npm", "run", "build"], results, cwd=WEB)
    stage("deploy console release", [python, "-m", "streamscale_console", "deploy"], results)
    stage(
        "live cluster acceptance",
        [python, "-m", "pytest", "console/tests/live", "-m", "live", "-q"],
        results,
    )
    process, log = start_server()
    try:
        stage("browser acceptance", ["npx", "playwright", "test"], results, cwd=WEB)
    finally:
        process.terminate()
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        (ARTIFACTS / "server.json").unlink(missing_ok=True)
    summary = {"status": "passed", "started_at": started, "ended_at": now(), "stages": results}
    (ARTIFACTS / "check.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
