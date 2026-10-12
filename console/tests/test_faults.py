"""Fault supervisor: bounded, scoped faults that always recover, even across restarts."""

import json
import time

import pytest
from streamscale_console_worker import control
from streamscale_console_worker.faults import FaultError, FaultSupervisor, validate


class FakeKube:
    def __init__(self):
        self.calls = []
        self.pods = ["streamscale-console-consumer-a"]
        self.infra = {"postgres": 1, "kafka": 1}

    def console_pods(self):
        return [{"metadata": {"name": name}, "status": {"phase": "Running"}} for name in self.pods]

    def delete_console_pod(self, name):
        self.calls.append(("delete", name))

    def resize_cpu(self, name, limit, request=None):
        self.calls.append(("resize", name, limit, request))

    def scale_infrastructure(self, role, replicas):
        self.calls.append(("scale", role, replicas))
        self.infra[role] = replicas

    def statefulset(self, role):
        return {"spec": {"replicas": self.infra[role]}}


class FakeObserver:
    class context:
        run = {"run_id": "r"}

    @staticmethod
    def latest():
        return {}


class FakeAgent:
    def __init__(self, kube):
        self.kube = kube
        self.components = {}
        self.observer = FakeObserver()


@pytest.fixture
def supervisor(tmp_path):
    kube = FakeKube()
    return FaultSupervisor(FakeAgent(kube), root=tmp_path), kube


def tick_all(supervisor):
    with supervisor.lock:
        for fault in list(supervisor.state["active"]):
            supervisor.tick(fault)
    supervisor.save()


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("postgres_outage", {"duration_s": 121}),
        ("postgres_outage", {"duration_s": 2}),
        ("slow_processing", {"delay_ms": 5000}),
        ("restrict_cpu", {"limit_m": 10}),
        ("restart_consumer", {}),
        ("restart_consumer", {"pod": "streamscale-kafka-0"}),
        ("drop_database", {}),
    ],
)
def test_out_of_bounds_or_unscoped_faults_are_refused(kind, params):
    with pytest.raises(FaultError):
        validate(kind, params)


def test_only_one_infrastructure_outage_at_a_time(supervisor):
    faults, _ = supervisor
    faults.inject({"kind": "postgres_outage", "params": {"duration_s": 20}})
    with pytest.raises(FaultError):
        faults.inject({"kind": "kafka_outage", "params": {"duration_s": 20}})
    with pytest.raises(FaultError):
        faults.inject({"kind": "postgres_outage", "params": {"duration_s": 20}})


def test_an_outage_is_restored_when_its_time_is_up(supervisor):
    faults, kube = supervisor
    faults.inject({"kind": "postgres_outage", "params": {"duration_s": 20}})
    assert kube.infra["postgres"] == 0
    faults.state["active"][0]["expires_at"] = time.time() - 1
    tick_all(faults)
    assert kube.infra["postgres"] == 1 and not faults.state["active"]
    assert faults.state["history"][-1]["state"] == "recovered"


def test_cpu_restriction_reaches_new_pods_and_is_restored(supervisor):
    faults, kube = supervisor
    faults.inject({"kind": "restrict_cpu", "params": {"duration_s": 30, "limit_m": 100}})
    kube.pods.append("streamscale-console-consumer-b")
    tick_all(faults)
    restricted = [call for call in kube.calls if call[0] == "resize" and call[2] == "100m"]
    assert {call[1] for call in restricted} == set(kube.pods)
    faults.state["active"][0]["expires_at"] = time.time() - 1
    tick_all(faults)
    restored = [call for call in kube.calls if call[0] == "resize" and call[2] == "500m"]
    assert {call[1] for call in restored} == set(kube.pods)
    assert all(call[3] == "100m" for call in restored)


def test_consumer_directives_are_written_for_the_consumers(supervisor, tmp_path):
    faults, _ = supervisor
    faults.inject({"kind": "slow_processing", "params": {"duration_s": 30, "delay_ms": 80}})
    written = json.loads(control.faults_path(tmp_path).read_text())["faults"]
    assert [row["kind"] for row in written] == ["slow_processing"]
    assert written[0]["params"]["delay_ms"] == 80


def test_a_one_shot_crash_records_its_claim(supervisor, tmp_path):
    faults, _ = supervisor
    result = faults.inject({"kind": "crash_after_db_commit", "params": {}})
    claim = {"pod": "p", "event_id": "e" * 36, "partition": 1, "offset": 7, "exit_code": 75}
    control.claim(result["id"], claim, tmp_path)
    tick_all(faults)
    fault = faults.state["active"][0]
    assert fault["state"] == "fired" and fault["claim"]["offset"] == 7
    assert "exited 75" in fault["events"][-1]["text"]


def test_recover_all_restores_everything_and_infrastructure(supervisor):
    faults, kube = supervisor
    faults.inject({"kind": "kafka_outage", "params": {"duration_s": 60}})
    faults.inject({"kind": "restrict_db_cpu", "params": {"duration_s": 60, "limit_m": 50}})
    faults.recover_all({})
    assert not faults.state["active"]
    assert kube.infra["kafka"] == 1
    assert ("resize", "streamscale-postgres-0", "500m", "100m") in kube.calls


def test_faults_survive_an_agent_restart_and_still_recover(tmp_path):
    kube = FakeKube()
    first = FaultSupervisor(FakeAgent(kube), root=tmp_path)
    first.inject({"kind": "postgres_outage", "params": {"duration_s": 20}})
    restarted = FaultSupervisor(FakeAgent(kube), root=tmp_path)
    assert restarted.state["active"][0]["kind"] == "postgres_outage"
    restarted.state["active"][0]["expires_at"] = time.time() - 1
    tick_all(restarted)
    assert kube.infra["postgres"] == 1
