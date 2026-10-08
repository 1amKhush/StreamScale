import json
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest
from prometheus_client import CollectorRegistry

from streamscale.metrics import ConsumerMetrics, WorkloadTotals, measured_database
from streamscale.storage import WriteResult

EVENT = json.loads((Path(__file__).parent / "fixtures/order-created.v1.json").read_text())
RUN = {
    "run_id": EVENT["run_id"],
    "mode": "fixed",
    "scenario": "steady",
    "attempts": 7,
    "acknowledged": 7,
}
LABELS = {field: RUN[field] for field in ("run_id", "mode", "scenario")}


def test_unique_latency_excludes_duplicate_replay_and_counts_suppressed_state():
    collection = CollectorRegistry()
    metrics = ConsumerMetrics(RUN, collection)
    store = Mock()
    store.persist.side_effect = [WriteResult("accepted", False), WriteResult("duplicate", False)]
    measured_database(metrics, store, EVENT, 0, 1)
    measured_database(metrics, store, EVENT, 0, 1)
    assert collection.get_sample_value("streamscale_accepted_total", LABELS) == 1
    assert collection.get_sample_value("streamscale_duplicate_total", LABELS) == 1
    assert collection.get_sample_value("streamscale_late_total", LABELS) == 1
    assert collection.get_sample_value("streamscale_end_to_end_seconds_count", LABELS) == 1
    assert (
        collection.get_sample_value(
            "streamscale_db_seconds_count", LABELS | {"result": "duplicate"}
        )
        == 1
    )


def test_database_failure_has_a_bounded_stage_and_no_accepted_latency():
    collection = CollectorRegistry()
    metrics = ConsumerMetrics(RUN, collection)
    with pytest.raises(psycopg.OperationalError):
        measured_database(
            metrics, Mock(persist=Mock(side_effect=psycopg.OperationalError())), EVENT, 0, 1
        )
    assert collection.get_sample_value("streamscale_failed_total", LABELS | {"stage": "db"}) == 1
    assert collection.get_sample_value("streamscale_accepted_total", LABELS) == 0
    assert collection.get_sample_value("streamscale_end_to_end_seconds_count", LABELS) == 0


def test_workload_totals_remain_authoritative_across_exporter_recreation():
    for _ in range(2):
        collection = CollectorRegistry()
        collection.register(WorkloadTotals(RUN))
        assert collection.get_sample_value("streamscale_generated_total", LABELS) == 7
        assert collection.get_sample_value("streamscale_published_total", LABELS) == 7
        assert all(
            "event_id" not in sample.labels and "order_id" not in sample.labels
            for family in collection.collect()
            for sample in family.samples
        )
