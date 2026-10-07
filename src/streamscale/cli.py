"""Small development CLI; connection errors never print DSNs or passwords."""

import argparse
import json
import os
import sys

from streamscale.config import Settings
from streamscale.migrations import migrate
from streamscale.smoke import smoke
from streamscale.topics import ensure_topics


def main() -> int:
    parser = argparse.ArgumentParser(prog="streamscale")
    parser.add_argument("command", choices=("topics", "migrate", "smoke"))
    args = parser.parse_args()
    try:
        if args.command == "topics":
            bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:19092").strip()
            if not bootstrap:
                raise ValueError("KAFKA_BOOTSTRAP_SERVERS must not be empty")
            result = {"topics": ensure_topics(bootstrap)}
        elif args.command == "migrate":
            result = {"applied": migrate(Settings.from_env().database_url)}
        else:
            result = smoke(Settings.from_env())
    except Exception as exc:
        # Driver exceptions may contain connection details; show only safe own messages.
        detail = str(exc) if type(exc) in {ValueError, RuntimeError} else type(exc).__name__
        print(f"{args.command} failed: {detail}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
