"""Console entry points: serve the local console or deploy its cluster components."""

import argparse
import json
import os
import secrets
import signal
import socket
import sys
from contextlib import ExitStack

import uvicorn

from streamscale_console.cluster import ARTIFACTS, ROOT, ClusterError

PREFERRED_PORT = 18080


def choose_port(requested):
    port = PREFERRED_PORT if requested is None else requested
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return probe.getsockname()[1]
        except OSError:
            if requested is not None and requested != 0:
                raise SystemExit(
                    f"Loopback port {port} is already in use. Choose another --port, "
                    "or use --port 0 for an available port."
                ) from None
    print(f"Console port {PREFERRED_PORT} is in use; choosing an available port.", flush=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def open_dashboards(stack, grafana_port=None, prometheus_port=None):
    """Grafana/Prometheus forwards for console links, with the launcher's port fallback."""
    sys.path.insert(0, str(ROOT / "deploy"))
    from ui import dashboard_forward  # shares deploy/ui.py's busy-port fallback

    return {
        "grafana": dashboard_forward(stack, "grafana", grafana_port) + "/d/streamscale",
        "prometheus": dashboard_forward(stack, "prometheus", prometheus_port),
    }


def serve(args):
    from streamscale_console.app import Console, create_app, register_core_operations
    from streamscale_console.components import attach

    port = choose_port(args.port)
    token = secrets.token_urlsafe(32)
    console = Console(token, port)
    register_core_operations(console)
    attach(console)
    url = f"http://127.0.0.1:{port}"
    login = f"{url}/?token={token}"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    record = ARTIFACTS / "server.json"
    with open(record, "w", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
        json.dump({"url": url, "login_url": login, "port": port, "pid": os.getpid()}, output)
    record.chmod(0o600)
    with ExitStack() as stack:
        if args.dashboards:
            try:
                console.dashboards = open_dashboards(stack, args.grafana_port, args.prometheus_port)
            except RuntimeError as error:
                print(f"Dashboard links unavailable: {error}", flush=True)
        print(f"StreamScale console: {login}", flush=True)
        if console.dashboards:
            print(
                f"Grafana: {console.dashboards['grafana']}\n"
                f"Prometheus: {console.dashboards['prometheus']}",
                flush=True,
            )
        print(
            "Keep this command running. Ctrl+C closes the console and its forwards; "
            "an active sale and fault-recovery timers continue in the cluster agent.",
            flush=True,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(console),
                host="127.0.0.1",
                port=port,
                log_level="warning",
                ws_ping_interval=20,
                ws_ping_timeout=20,
            )
        )
        signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
        server.run()
    record.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(prog="streamscale_console", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serving = commands.add_parser("serve", help="Serve the console on a loopback port")
    serving.add_argument("--port", type=int, help="Default 18080; 0 picks an available port")
    serving.add_argument(
        "--dashboards", action="store_true", help="Also forward Grafana/Prometheus for links"
    )
    serving.add_argument("--grafana-port", type=int, help="With --dashboards; 0 picks any")
    serving.add_argument("--prometheus-port", type=int, help="With --dashboards; 0 picks any")
    commands.add_parser("deploy", help="Build/load the console image and install its release")
    commands.add_parser("check", help="Unit, web, live-cluster and browser acceptance")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            serve(args)
        elif args.command == "check":
            from streamscale_console.check import main as check

            check()
        else:
            from streamscale_console.deploy import deploy

            print(json.dumps(deploy(), indent=2))
    except ClusterError as error:
        raise SystemExit(f"Console {args.command} failed: {error}") from None


if __name__ == "__main__":
    main()
