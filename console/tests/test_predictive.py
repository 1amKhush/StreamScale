from copy import deepcopy
from types import SimpleNamespace

import pytest

from .test_scaling import FakeAgent, FakeKube, Scaling, wait


@pytest.fixture
def controller(tmp_path, monkeypatch):
    kube = FakeKube()
    agent = FakeAgent(kube)
    sample = {
        "sampled_at": 100,
        "t": 100,
        "run_id": "run",
        "high": [100] * 3,
        "position": [100] * 3,
        "lag": [0] * 3,
        "ready": 1,
        "desired": 1,
        "assignments": [[0, 1, 2]],
        "stable": True,
    }
    agent.observer = SimpleNamespace(latest=lambda: sample)
    scaling = Scaling(agent, root=tmp_path)
    scaling.state["mode"] = "ml-predictive"
    predictor = scaling.predictive
    predictor.artifact = (
        "model",
        {},
        {"calibration": {"capacity": {1: 30, 2: 45, 3: 60}, "actuation_seconds": 3}},
    )
    monkeypatch.setattr("streamscale_console_worker.predictive.observation", lambda s: deepcopy(s))
    return predictor, scaling, kube, sample


def test_stale_data_holds_and_mode_changes_exclude_inflight_writes(controller):
    predictor, scaling, kube, sample = controller
    with pytest.raises(ValueError, match="Stale"):
        predictor.tick(120)
    assert not kube.calls
    scaling.work.acquire()
    predictor.tick(100)
    scaling.work.release()
    assert not kube.calls
    scaling.set_mode({"mode": "fixed"})
    assert wait(scaling)["state"] == "succeeded"
    calls = list(kube.calls)
    predictor.tick(100)
    assert kube.calls == calls


def test_predictive_override_and_release_retain_single_ownership(controller):
    predictor, scaling, kube, sample = controller
    scaling.enter_override({"replicas": 0})
    assert wait(scaling)["state"] == "succeeded"
    predictor.tick(100)
    assert predictor.state["status"] == "manual override" and kube.replicas == 0
    scaling.release_override({})
    assert wait(scaling)["state"] == "succeeded"
    assert not kube.hpa and not kube.keda and kube.replicas >= 1


def test_warmup_reactive_fallback_scales_without_repeated_writes(controller):
    predictor, scaling, kube, sample = controller
    predictor.tick(100)
    sample.update(sampled_at=101, t=101, high=[200] * 3, lag=[100] * 3)
    predictor.tick(101)
    assert predictor.state["applied"] and kube.replicas == 3
    sample.update(sampled_at=107, t=107)
    predictor.tick(107)
    assert kube.calls.count("scale-3") == 1
