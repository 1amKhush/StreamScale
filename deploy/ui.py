"""Interactive dashboards with available-port fallback and scoped forward cleanup."""

import argparse
import signal
import sys
from contextlib import ExitStack
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent / "k8s"))

from access import forward  # noqa: E402


def dashboard_forward(stack, role, requested_port):
    preferred = {"grafana": 13000, "prometheus": 19090}[role]
    port = preferred if requested_port is None else requested_port
    try:
        return stack.enter_context(forward(role, port))
    except RuntimeError as error:
        if str(error) != f"Loopback port {port} is already in use":
            raise
        if requested_port is not None:
            raise RuntimeError(
                f"Loopback port {port} is already in use. "
                f"Choose another --{role}-port, or use --{role}-port 0 for an available port."
            ) from None
        print(
            f"{role.capitalize()} port {preferred} is in use; choosing an available port.",
            flush=True,
        )
        return stack.enter_context(forward(role))


def port_number(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Use a port from 0 to 65535 (0 chooses an available port)"
        ) from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("Use a port from 0 to 65535 (0 chooses an available port)")
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grafana-port",
        type=port_number,
        help="Default: 13000, or an available port when busy. Use 0 for any available port.",
    )
    parser.add_argument(
        "--prometheus-port",
        type=port_number,
        help="Default: 19090, or an available port when busy. Use 0 for any available port.",
    )
    args = parser.parse_args(argv)
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        with ExitStack() as stack:
            grafana = dashboard_forward(stack, "grafana", args.grafana_port)
            prometheus = dashboard_forward(stack, "prometheus", args.prometheus_port)
            print(f"Grafana: {grafana}/d/streamscale\nPrometheus: {prometheus}", flush=True)
            print(
                "Login: artifacts/k8s/credentials.json. "
                "Keep this command running; Ctrl+C stops this command's forwards.",
                flush=True,
            )
            while not stop.wait(1):
                pass
    except RuntimeError as error:
        parser.exit(1, f"Dashboard access failed: {error}\n")


if __name__ == "__main__":
    main()
