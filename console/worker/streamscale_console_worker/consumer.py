"""Console consumer: the unchanged StreamScale consume loop following the active sale.

One process keeps a metrics endpoint (9100) and a health/state endpoint (9101) for
its whole life and runs ``streamscale.consumer.consume`` for the console run that
``/runs/console/current.json`` names. Test-only fault hooks wrap two functions in
this process; the measured image and its source are not modified.
"""

import json
import os
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

from prometheus_client import CollectorRegistry

import streamscale.consumer as consumer_module
import streamscale.dlq as dlq_module
from streamscale.config import Settings
from streamscale.metrics import ConsumerMetrics, MetricsServer, registry
from streamscale.run_artifacts import utc_now
from streamscale.service import wait_run
from streamscale_console_worker import control

CRASH_EXIT_CODES = {"crash_after_db_commit": 75, "crash_after_dlq_ack": 76}


class WorkerState:
    def __init__(self, pod):
        self.lock = threading.Lock()
        self.values = {
            "pod": pod,
            "state": "starting",
            "run_name": None,
            "run_id": None,
            "assigned": [],
            "loop_restarts": 0,
            "last_error": None,
            "started_at": utc_now(),
            "active_faults": [],
        }

    def update(self, **fields):
        with self.lock:
            self.values.update(fields)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.values))

    def failed(self, error):
        # Exception text from drivers can contain connection details; keep our own only.
        own = type(error).__module__.startswith("streamscale") or type(error) in {
            RuntimeError,
            ValueError,
        }
        with self.lock:
            self.values["loop_restarts"] += 1
            self.values["last_error"] = {
                "type": type(error).__name__,
                "message": str(error)[:240] if own else type(error).__name__,
                "at": utc_now(),
            }


class Delegate:
    """Expose the active run's registry through one long-lived metrics endpoint."""

    def __init__(self):
        self.current = None

    def collect(self):
        if self.current is not None:
            yield from self.current.collect()


class OwnedGauge:
    """Mirror partition ownership updates into worker state while updating the gauge."""

    def __init__(self, gauge, state):
        self.gauge = gauge
        self.state = state

    def set(self, value):
        self.gauge.set(value)
        if not value:
            self.state.update(assigned=[])


class HealthServer:
    def __init__(self, state, port, host="0.0.0.0"):
        def handler_factory(*args):
            return _Health(state, *args)

        self.server = ThreadingHTTPServer((host, port), handler_factory)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _Health(BaseHTTPRequestHandler):
    def __init__(self, state, *args):
        self.state = state
        super().__init__(*args)

    def do_GET(self):
        values = self.state.snapshot()
        if self.path == "/health/live":
            status, payload = 200, b"live\n"
        elif self.path == "/health/ready":
            ready = values["state"] in {"idle", "waiting", "consuming"}
            status, payload = (200, b"ready\n") if ready else (503, b"not ready\n")
        elif self.path == "/state":
            status, payload = 200, json.dumps(values).encode()
        else:
            status, payload = 404, b"not found\n"
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class FaultHooks:
    """Read console fault directives and inject them at exact handling boundaries."""

    def __init__(self, pod, state, root=control.ROOT):
        self.pod = pod
        self.state = state
        self.root = root
        self.faults = {}
        self.signature = None
        self.memory_thread = None

    def install(self):
        original_database = consumer_module.measured_database
        original_publish = dlq_module.DeadLetterQueue.publish

        def measured_database(metrics, store, event, partition, offset):
            delay = self.delay_seconds()
            if delay:
                time.sleep(delay)
            result = original_database(metrics, store, event, partition, offset)
            # The PostgreSQL transaction has committed; the source offset has not.
            self.crash(
                "crash_after_db_commit",
                {
                    "event_id": event["event_id"],
                    "order_id": event["order_id"],
                    "partition": partition,
                    "offset": offset,
                    "outcome": result.outcome,
                },
            )
            return result

        def publish(dlq, message, error):
            original_publish(dlq, message, error)
            # The broker acknowledged the DLQ envelope; the source offset is uncommitted.
            self.crash(
                "crash_after_dlq_ack",
                {
                    "partition": message.partition(),
                    "offset": message.offset(),
                    "error_code": error.code,
                },
            )

        consumer_module.measured_database = measured_database
        dlq_module.DeadLetterQueue.publish = publish

    def applies(self, fault):
        return fault.get("pod") in (None, self.pod) and fault.get("expires_at", 1e18) > time.time()

    def refresh(self):
        path = control.faults_path(self.root)
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            signature = None
        if signature == self.signature:
            return
        self.signature = signature
        document = control.read_json(path) if signature else None
        faults = {}
        for fault in (document or {}).get("faults", []):
            if self.applies(fault):
                faults[fault["kind"]] = fault
        self.faults = faults
        self.state.update(active_faults=sorted(faults))
        memory = faults.get("memory_pressure")
        if memory and self.memory_thread is None:
            self.memory_thread = Thread(target=self.exhaust_memory, args=(memory,), daemon=True)
            self.memory_thread.start()

    def delay_seconds(self):
        fault = self.faults.get("slow_processing")
        if not fault or fault.get("expires_at", 1e18) <= time.time():
            return 0
        return min(int(fault["params"]["delay_ms"]), 2000) / 1000

    def crash(self, kind, detail):
        fault = self.faults.get(kind)
        if not fault or fault.get("expires_at", 1e18) <= time.time():
            return
        record = detail | {"pod": self.pod, "kind": kind, "exit_code": CRASH_EXIT_CODES[kind]}
        if control.claim(fault["id"], record, self.root):
            # Deliberate test-only exit: no finally blocks, no close, no offset commit.
            os._exit(CRASH_EXIT_CODES[kind])

    def exhaust_memory(self, fault):
        if not control.claim(fault["id"], {"pod": self.pod, "kind": "memory_pressure"}, self.root):
            return
        step = min(int(fault["params"].get("step_mib", 16)), 64) * 2**20
        retained = []
        deadline = time.monotonic() + 120
        # Bounded: stop at 2 GiB or two minutes even if no cgroup limit intervenes.
        while time.monotonic() < deadline and len(retained) * step < 2 * 2**30:
            retained.append(bytearray(b"\x01") * step)
            time.sleep(0.1)

    def watch(self, stop):
        while not stop.wait(0.5):
            try:
                self.refresh()
            except (OSError, ValueError, KeyError) as error:
                self.state.update(last_error={"type": type(error).__name__, "at": utc_now()})


def follow_pointer(pointer, run_stop, stop, root):
    while not run_stop.is_set():
        if stop.wait(0.5):
            break
        try:
            current = control.current_run(root)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if current is None or current["run_name"] != pointer["run_name"]:
            break
    run_stop.set()


def serve(settings, stop, pod, root=control.ROOT, metrics_port=9100, health_port=9101):
    state = WorkerState(pod)
    health = HealthServer(state, health_port)
    delegate = Delegate()
    # Process CPU/memory stay visible while idle; run counters live in a per-run registry.
    outer = registry()
    outer.register(delegate)
    metrics_server = MetricsServer(outer, metrics_port)
    hooks = FaultHooks(pod, state, root)
    hooks.install()
    hooks.refresh()
    Thread(target=hooks.watch, args=(stop,), daemon=True).start()
    cache = {}
    backoff = 1
    try:
        while not stop.is_set():
            try:
                pointer = control.current_run(root)
            except (OSError, ValueError, json.JSONDecodeError):
                pointer = None
            if pointer is None:
                state.update(state="idle", run_name=None, run_id=None, assigned=[])
                stop.wait(1)
                continue
            run_stop = Event()
            watcher = Thread(
                target=follow_pointer, args=(pointer, run_stop, stop, root), daemon=True
            )
            watcher.start()
            directory = Path(pointer["run_dir"])
            state.update(state="waiting", run_name=pointer["run_name"])
            try:
                if (control.read_json(directory / "run.json") or {}).get("status") == "failed":
                    # Nothing durable to drain; wait for the next sale instead of retrying.
                    state.update(state="idle", run_id=None, assigned=[])
                    run_stop.wait()
                    continue
                run = wait_run(directory, run_stop, timeout=3600, allow_publishing=True)
                if run is None:
                    continue
                if run["run_id"] not in cache:
                    cache.clear()
                    collection = CollectorRegistry(auto_describe=False)
                    metrics = ConsumerMetrics(run, collection)
                    metrics.owned = OwnedGauge(metrics.owned, state)
                    cache[run["run_id"]] = (collection, metrics)
                collection, metrics = cache[run["run_id"]]
                delegate.current = collection
                state.update(state="consuming", run_id=run["run_id"])
                consumer_module.consume(
                    settings,
                    directory=directory,
                    stay_running=True,
                    stop=run_stop,
                    metrics=metrics,
                    readiness=metrics_server.ready,
                    follow_publish=True,
                    on_assignment=lambda parts: state.update(assigned=parts),
                )
                backoff = 1
            except Exception as error:  # noqa: BLE001 - the loop reports and retries safely
                state.failed(error)
                state.update(state="retrying", assigned=[])
                run_stop.wait(backoff)
                backoff = min(backoff * 2, 8)
            finally:
                run_stop.set()
                watcher.join(2)
    finally:
        state.update(state="stopping")
        metrics_server.close()
        health.close()


def main():
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    pod = os.environ.get("POD_NAME") or socket.gethostname()
    serve(Settings.from_env(), stop, pod)


if __name__ == "__main__":
    main()
