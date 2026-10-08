"""Small development CLI; connection errors never print DSNs or passwords."""

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from threading import Event

from streamscale.config import Settings
from streamscale.migrations import migrate
from streamscale.smoke import smoke
from streamscale.storage import IntegrityFailure
from streamscale.topics import ensure_topics


def main() -> int:
    parser = argparse.ArgumentParser(prog="streamscale")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("topics", "migrate", "smoke"):
        commands.add_parser(name)
    workload = commands.add_parser("generate", help="Generate a fresh reproducible execution")
    workload.add_argument("--output", type=Path, required=True)
    workload.add_argument("--seed", type=int, default=42)
    workload.add_argument("--orders", type=int, default=100)
    workload.add_argument(
        "--scenario", choices=("steady", "flash-sale", "overload-recovery"), default="steady"
    )
    workload.add_argument(
        "--profile", choices=("normal", "correctness", "fixtures"), default="normal"
    )
    workload.add_argument("--mode", choices=("fixed", "cpu-hpa", "lag-keda"), default="fixed")
    workload.add_argument(
        "--group-id", help="Unique execution group, also used by the Kafka scaler"
    )
    publisher = commands.add_parser("publish", help="Register and publish this execution once")
    publisher.add_argument("--run-dir", type=Path, required=True)
    publisher.add_argument(
        "--no-wait", action="store_true", help="Skip pacing for local correctness demos"
    )
    worker = commands.add_parser(
        "consume", help="Sequential DB/DLQ handling with explicit Kafka commits"
    )
    worker.add_argument("--run-dir", type=Path)
    worker.add_argument("--group")
    worker.add_argument("--max-records", type=int)
    worker.add_argument("--idle-timeout", type=float, default=30)
    worker.add_argument("--batch-size", type=int, default=50)
    worker.add_argument("--audit-log", type=Path)
    worker.add_argument("--stay-running", action="store_true")
    worker.add_argument(
        "--crash-after-db-commit",
        type=int,
        help="Test only: hard exit 75 before the Nth source offset commit",
    )
    worker.add_argument(
        "--crash-after-dlq-ack",
        type=int,
        help="Test only: hard exit 76 after the Nth DLQ ack, before its source commit",
    )
    inspector = commands.add_parser(
        "inspect", help="Reconcile acknowledged events with SQL and DLQ"
    )
    inspector.add_argument("--run-dir", type=Path, required=True)
    inspector.add_argument("--group")
    initializer = commands.add_parser("bootstrap", help="Wait for cluster Kafka/DB and bootstrap")
    initializer.add_argument("--timeout", type=float, default=180)
    for name in ("serve", "exporter"):
        service = commands.add_parser(name)
        service.add_argument("--run-dir", type=Path, required=True)
        service.add_argument("--metrics-port", type=int, default=9100)
    args = parser.parse_args()
    try:
        if args.command == "topics":
            bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:19092").strip()
            if not bootstrap:
                raise ValueError("KAFKA_BOOTSTRAP_SERVERS must not be empty")
            result = {"topics": ensure_topics(bootstrap)}
        elif args.command == "migrate":
            result = {"applied": migrate(Settings.from_env().database_url)}
        elif args.command == "generate":
            from streamscale_workload.generator import generate

            result = generate(
                args.output,
                args.seed,
                args.orders,
                args.scenario,
                args.profile,
                args.mode,
                group_id=args.group_id,
            )
        elif args.command == "publish":
            from streamscale_workload.publisher import publish

            result = publish(args.run_dir, Settings.from_env(), paced=not args.no_wait)
        elif args.command == "consume":
            from streamscale.consumer import consume

            stop = Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            result = consume(
                Settings.from_env(),
                directory=args.run_dir,
                group_id=args.group,
                max_records=args.max_records,
                idle_timeout=args.idle_timeout,
                batch_size=args.batch_size,
                crash_after_db_commit=args.crash_after_db_commit,
                crash_after_dlq_ack=args.crash_after_dlq_ack,
                stay_running=args.stay_running,
                audit_log=args.audit_log,
                stop=stop,
            )
        elif args.command == "inspect":
            from streamscale.inspection import inspect_run

            result = inspect_run(args.run_dir, Settings.from_env(), args.group)
        elif args.command == "bootstrap":
            from streamscale.service import bootstrap

            result = bootstrap(Settings.from_env(), args.timeout)
        elif args.command in {"serve", "exporter"}:
            from streamscale.service import serve_consumer, serve_exporter

            stop = Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            service = serve_consumer if args.command == "serve" else serve_exporter
            result = service(Settings.from_env(), args.run_dir, args.metrics_port, stop)
        else:
            result = smoke(Settings.from_env())
    except KeyboardInterrupt:
        print(
            "Stopped; restart the consumer with the same group to replay uncommitted records",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        # Driver exceptions may contain connection details; show only safe own messages.
        detail = (
            str(exc)
            if type(exc) in {ValueError, RuntimeError, IntegrityFailure}
            else type(exc).__name__
        )
        print(f"{args.command} failed: {detail}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
