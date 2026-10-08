"""Raw committed SQL timings; measurements do not depend on process counter resets."""

from uuid import UUID

import psycopg

from streamscale.run_artifacts import read_run


def export_measurements(directory, settings):
    run = read_run(directory)
    if run["status"] != "published":
        raise RuntimeError("Measurement export requires a successfully published run")
    with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
        rows = connection.execute(
            """SELECT event_id::text, produced_at, ingested_at, kafka_partition, kafka_offset
               FROM order_event WHERE run_id = %s ORDER BY ingested_at, event_id""",
            (UUID(run["run_id"]),),
        ).fetchall()
        ended = connection.execute(
            "SELECT ended_at FROM experiment_run WHERE run_id = %s", (UUID(run["run_id"]),)
        ).fetchone()
    return {
        "status": "collected",
        "run_id": run["run_id"],
        "ended_at": ended[0].isoformat() if ended and ended[0] else None,
        "facts": [
            {
                "event_id": row[0],
                "produced_at": row[1].isoformat(),
                "ingested_at": row[2].isoformat(),
                "partition": row[3],
                "offset": row[4],
            }
            for row in rows
        ],
    }
