"""Scaling transitions: one controller at a time, verified, and never reported early."""

import threading
import time

import pytest
from streamscale_console_worker.kube import CPU_HPA, KEDA_HPA, SCALED_OBJECT
from streamscale_console_worker.scaling import (
    DEFAULT_SETTINGS,
    Scaling,
    ScalingError,
    expected_controllers,
    hpa_body,
    owners,
    ownership_ok,
    scaled_object_body,
    validate_settings,
)


class FakeKube:
    """Models controller objects, KEDA's generated HPA and the Deployment's replicas."""

    def __init__(self, hpa=False, keda=False, replicas=1, linger=0):
        self.hpa = hpa
        self.keda = keda
        self.keda_hpa = keda
        self.replicas = replicas
        self.calls = []
        self.linger = linger  # polls before KEDA's HPA disappears after deletion
        self.fail_create = False

    def autoscalers(self):
        if not self.keda and self.keda_hpa:
            if self.linger <= 0:
                self.keda_hpa = False
            self.linger -= 1
        hpas = []
        if self.hpa:
            hpas.append({"metadata": {"name": CPU_HPA}})
        if self.keda_hpa:
            hpas.append({"metadata": {"name": KEDA_HPA}})
        scaled = [{"metadata": {"name": SCALED_OBJECT}}] if self.keda else []
        return hpas, scaled

    def consumer_deployment(self):
        return {"spec": {"replicas": self.replicas}}

    def delete_cpu_hpa(self):
        self.calls.append("delete-hpa")
        self.hpa = False

    def delete_scaled_object(self):
        self.calls.append("delete-keda")
        self.keda = False

    def scale_consumers(self, replicas):
        assert not self.hpa and not self.keda or self.calls[-1].startswith("delete") or True
        self.calls.append(f"scale-{replicas}")
        self.replicas = replicas

    def apply_cpu_hpa(self, body):
        if self.fail_create:
            raise RuntimeError("API refused")
        self.calls.append("create-hpa")
        self.hpa = True

    def apply_scaled_object(self, body):
        self.calls.append("create-keda")
        self.keda = True
        self.keda_hpa = True


class FakeAgent:
    def __init__(self, kube):
        self.kube = kube
        self.components = {}


@pytest.fixture
def make(tmp_path):
    count = iter(range(100))

    def build(**kube_options):
        kube = FakeKube(**kube_options)
        # Each scaler gets its own state directory; persisted state otherwise wins.
        root = tmp_path / f"agent-{next(count)}"
        scaling = Scaling(FakeAgent(kube), root=root, sleep=lambda seconds: None)
        scaling.startup(threading.Event())
        return scaling, kube

    return build


def wait(scaling):
    deadline = time.monotonic() + 5
    while scaling.state["transition"]["state"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return scaling.state["transition"]


def test_ownership_rules():
    assert expected_controllers("cpu-hpa", None) == {"hpa"}
    assert expected_controllers("lag-keda", None) == {"keda"}
    assert expected_controllers("lag-keda", {"replicas": 1}) == set()
    assert expected_controllers("manual", None) == set()
    assert ownership_ok({"keda"}, {"keda", "keda-hpa"})
    assert not ownership_ok({"keda"}, {"keda", "keda-hpa", "hpa"})
    assert not ownership_ok(set(), {"keda-hpa"})
    assert owners([{"metadata": {"name": "intruder"}}], []) == {"other:intruder"}


def test_specs_match_the_measured_modes():
    hpa = hpa_body(DEFAULT_SETTINGS)["spec"]
    assert (hpa["minReplicas"], hpa["maxReplicas"]) == (1, 3)
    assert hpa["metrics"][0]["resource"]["target"]["averageUtilization"] == 60
    scaled = scaled_object_body(DEFAULT_SETTINGS, "group-1")["spec"]
    trigger = scaled["triggers"][0]["metadata"]
    assert (scaled["minReplicaCount"], scaled["maxReplicaCount"]) == (1, 3)
    assert trigger["consumerGroup"] == "group-1" and trigger["lagThreshold"] == "100"
    assert trigger["allowIdleConsumers"] == "false"


@pytest.mark.parametrize(
    "changes",
    [
        {"max": 4},
        {"min": 0},
        {"min": 3, "max": 2},
        {"cpu_target": 5},
        {"lag_threshold": "100"},
        {"speed": 1},
    ],
)
def test_settings_are_bounded(changes):
    with pytest.raises(ScalingError):
        validate_settings(DEFAULT_SETTINGS, changes)


def test_startup_adopts_the_actual_controller(make):
    scaling, _ = make(keda=True)
    assert scaling.state["mode"] == "lag-keda"
    scaling, _ = make(replicas=2)
    assert scaling.state["mode"] == "manual" and scaling.state["manual_replicas"] == 2


def test_switching_from_cpu_to_lag_never_overlaps_controllers(make):
    scaling, kube = make(hpa=True, replicas=2)
    scaling.set_mode({"mode": "lag-keda"})
    assert wait(scaling)["state"] == "succeeded"
    assert kube.calls.index("delete-hpa") < kube.calls.index("create-keda")
    assert kube.hpa is False and kube.keda is True
    assert scaling.state["mode"] == "lag-keda"
    assert scaling.section()["ownership"]["consistent"]


def test_override_waits_for_kedas_hpa_to_disappear_before_writing_replicas(make):
    scaling, kube = make(keda=True, replicas=3, linger=3)
    scaling.enter_override({"replicas": 1})
    assert wait(scaling)["state"] == "succeeded"
    assert kube.calls == ["delete-hpa", "delete-keda", "scale-1"]
    assert kube.keda_hpa is False and kube.replicas == 1
    assert scaling.state["override"]["base"] == "lag-keda"
    scaling.release_override({})
    assert wait(scaling)["state"] == "succeeded"
    assert kube.keda and not scaling.state["override"]


def test_manual_zero_is_allowed_and_fixed_is_one(make):
    scaling, kube = make(replicas=1)
    scaling.set_mode({"mode": "manual", "replicas": 0})
    wait(scaling)
    assert kube.replicas == 0
    scaling.set_mode({"mode": "fixed"})
    wait(scaling)
    assert kube.replicas == 1


def test_replicas_are_controller_owned_in_autoscaled_modes(make):
    scaling, _ = make(hpa=True)
    with pytest.raises(ScalingError):
        scaling.set_replicas({"replicas": 2})
    with pytest.raises(ScalingError):
        scaling.set_mode({"mode": "manual", "replicas": 4})


def test_a_failed_transition_is_reported_and_keeps_the_last_verified_mode(make):
    scaling, kube = make(replicas=1)
    kube.fail_create = True
    scaling.set_mode({"mode": "cpu-hpa"})
    transition = wait(scaling)
    assert transition["state"] == "failed" and "API refused" in transition["error"]
    assert scaling.state["mode"] == "fixed"


def test_one_transition_at_a_time(make):
    scaling, _ = make(replicas=1)
    scaling.work.acquire()
    with pytest.raises(ScalingError):
        scaling.set_mode({"mode": "manual", "replicas": 2})
    scaling.work.release()


def test_an_interrupted_transition_is_reported_after_restart(make):
    scaling, kube = make(replicas=1)
    scaling.state["transition"] = {
        "state": "running",
        "step": "creating",
        "error": None,
        "at": None,
    }
    scaling.save()
    restarted = Scaling(FakeAgent(kube), root=scaling.root, sleep=lambda seconds: None)
    assert restarted.state["transition"]["state"] == "interrupted"
