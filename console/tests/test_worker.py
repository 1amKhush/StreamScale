"""Shared control files, one-shot fault claims, lock discovery and metric parsing."""

import json
import os
import threading
from pathlib import Path

import pytest
from streamscale_console.workflow import WorkflowBusy, WorkflowLock, holder
from streamscale_console_worker import control
from streamscale_console_worker.consumer import FaultHooks, WorkerState
from streamscale_console_worker.observe import parse_metrics, pod_summary

METRICS = """
# TYPE streamscale_accepted_total counter
streamscale_accepted_total{run_id="r"} 12.0
# TYPE streamscale_duplicate_total counter
streamscale_duplicate_total{run_id="r"} 2.0
# TYPE streamscale_failed_total counter
streamscale_failed_total{run_id="r",stage="db"} 1.0
# TYPE streamscale_db_seconds histogram
streamscale_db_seconds_bucket{run_id="r",result="accepted",le="0.01"} 10.0
streamscale_db_seconds_bucket{run_id="r",result="accepted",le="+Inf"} 12.0
streamscale_db_seconds_bucket{run_id="r",result="failed",le="0.01"} 0.0
streamscale_db_seconds_bucket{run_id="r",result="failed",le="+Inf"} 5.0
streamscale_db_seconds_count{run_id="r",result="accepted"} 12.0
streamscale_db_seconds_count{run_id="r",result="failed"} 5.0
# TYPE process_cpu_seconds_total counter
process_cpu_seconds_total 3.5
"""


def test_metric_parsing_excludes_failed_db_attempts_from_latency():
    values = parse_metrics(METRICS)
    assert values["accepted"] == 12 and values["duplicate"] == 2
    assert values["failed"] == {"db": 1}
    assert values["db"]["count"] == 12
    assert values["db"]["buckets"][-1][1] == 12
    assert values["cpu_seconds"] == 3.5
    assert values["invalid"] is None  # absent, not zero


def test_one_shot_claim_has_exactly_one_winner(tmp_path):
    winners = []
    barrier = threading.Barrier(8)

    def contend(index):
        barrier.wait()
        if control.claim("fault-1", {"pod": f"p{index}"}, tmp_path):
            winners.append(index)

    threads = [threading.Thread(target=contend, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(winners) == 1
    assert control.read_claim("fault-1", tmp_path)["pod"] == f"p{winners[0]}"


def test_run_pointer_cannot_escape_the_console_directory(tmp_path):
    control.current_path(tmp_path).write_text(
        json.dumps({"run_name": "x", "run_dir": "/runs/run-1"})
    )
    with pytest.raises(ValueError):
        control.current_run(tmp_path)


def test_pointer_requires_a_generated_run(tmp_path):
    with pytest.raises(RuntimeError):
        control.set_current_run("missing", tmp_path)
    (tmp_path / "runs/sale-1").mkdir(parents=True)
    (tmp_path / "runs/sale-1/run.json").write_text("{}")
    control.set_current_run("sale-1", tmp_path)
    assert control.current_run(tmp_path)["run_dir"] == str(tmp_path / "runs/sale-1")


def test_fault_hooks_apply_only_to_their_pod_and_expire(tmp_path):
    state = WorkerState("pod-a")
    hooks = FaultHooks("pod-a", state, tmp_path)
    control.write_faults(
        [
            {"id": "f1", "kind": "slow_processing", "pod": None, "params": {"delay_ms": 250}},
            {"id": "f2", "kind": "crash_after_db_commit", "pod": "pod-b", "params": {}},
            {
                "id": "f3",
                "kind": "crash_after_dlq_ack",
                "pod": "pod-a",
                "params": {},
                "expires_at": 1,
            },
        ],
        tmp_path,
    )
    hooks.refresh()
    assert hooks.delay_seconds() == 0.25
    assert set(hooks.faults) == {"slow_processing"}
    assert state.snapshot()["active_faults"] == ["slow_processing"]


def test_processing_delay_is_capped(tmp_path):
    hooks = FaultHooks("pod-a", WorkerState("pod-a"), tmp_path)
    control.write_faults(
        [{"id": "f1", "kind": "slow_processing", "params": {"delay_ms": 60000}}], tmp_path
    )
    hooks.refresh()
    assert hooks.delay_seconds() == 2


def test_pod_summary_reports_last_termination_and_resize():
    pod = {
        "metadata": {"name": "c-1", "creationTimestamp": "t0"},
        "spec": {"containers": [{"resources": {"limits": {"cpu": "500m"}}}]},
        "status": {
            "phase": "Running",
            "podIP": "10.0.0.1",
            "conditions": [
                {"type": "Ready", "status": "False"},
                {"type": "PodResizeInProgress", "status": "True"},
            ],
            "containerStatuses": [
                {
                    "restartCount": 2,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                    "resources": {"limits": {"cpu": "100m"}},
                }
            ],
        },
    }
    summary = pod_summary(pod)
    assert summary["last_termination"]["reason"] == "OOMKilled"
    assert summary["cpu_limit"] == "100m"  # actual allocation, not the template
    assert summary["resize"] == ["PodResizeInProgress"]
    assert summary["waiting_reason"] == "CrashLoopBackOff"
    assert summary["ready"] is False


def test_workflow_holder_is_found_through_proc_locks(tmp_path):
    lock_file = tmp_path / "workflow.lock"
    lock_file.write_text(json.dumps({"started_at": "2026-10-12T00:00:00Z"}))
    stat = lock_file.stat()
    table = tmp_path / "locks"
    table.write_text(
        f"1: FLOCK  ADVISORY  WRITE {os.getpid() + 1} "
        f"{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{stat.st_ino} 0 EOF\n"
        "2: POSIX  ADVISORY  WRITE 99 00:00:1 0 EOF\n"
    )
    found = holder(lock_file, table)
    assert found["pid"] == os.getpid() + 1 and not found["self"]
    table.write_text("")
    assert holder(lock_file, table) is None


def test_console_lock_excludes_another_holder(tmp_path):
    path = tmp_path / "workflow.lock"
    first, second = WorkflowLock(path), WorkflowLock(path)
    first.acquire("sale one")
    with pytest.raises(WorkflowBusy):
        second.acquire("sale two")
    assert "interactive console" in json.loads(Path(path).read_text())["purpose"]
    first.release()
    second.acquire("sale two")
    second.release()
