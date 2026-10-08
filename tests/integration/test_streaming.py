"""The service consumes while the offline publisher is still pacing the workload."""

import time
from threading import Event, Thread
from uuid import UUID

import psycopg
import pytest
from streamscale_workload.generator import generate
from streamscale_workload.publisher import publish

from streamscale.config import Settings
from streamscale.consumer import consume
from streamscale.inspection import inspect_run
from streamscale.service import wait_run

pytestmark = pytest.mark.integration


def test_consumer_handles_live_records_before_paced_publication_finishes(
    settings,
    scratch_database,
    tmp_path,
):
    local = Settings(settings.kafka_bootstrap_servers, scratch_database)
    directory = tmp_path / "streaming"
    generated = generate(directory, orders=2, profile="fixtures")
    failures, summaries = [], []
    stop = Event()

    def publisher():
        try:
            publish(directory, local, paced=True)  # Final deliberately late event at ten seconds.
        except Exception as error:
            failures.append(error)

    def worker():
        try:
            wait_run(directory, stop, allow_publishing=True)
            summaries.append(consume(local, directory=directory, follow_publish=True, stop=stop))
        except Exception as error:
            failures.append(error)

    producer = Thread(target=publisher, daemon=True)
    consumer = Thread(target=worker, daemon=True)
    try:
        producer.start()
        consumer.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            assert not failures, failures
            with psycopg.connect(scratch_database) as connection:
                count = connection.execute(
                    "SELECT count(*) FROM order_event WHERE run_id=%s", (UUID(generated["run_id"]),)
                ).fetchone()[0]
            if count:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Consumer did not accept facts during publication")
        assert producer.is_alive(), "Publisher finished before live consumption was observed"
        producer.join(20)
        consumer.join(20)
        assert not producer.is_alive() and not consumer.is_alive()
        assert not failures, failures
        assert summaries[0]["status"] == "drained"
        assert summaries[0]["accepted"] == 4
        assert summaries[0]["invalid"] == 2
        assert inspect_run(directory, local)["status"] == "passed"
    finally:
        stop.set()
        producer.join(20)
        consumer.join(20)
