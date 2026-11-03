import json

import pytest

from streamscale.predictive.data import History, windows
from streamscale.predictive.model import load, publish, train


def trace(identifier="one", multiplier=1):
    rows = []
    total = 0
    for t in range(160):
        total += multiplier * (2 + t // 30)
        rows.append(
            {
                "t": t,
                "run_id": identifier,
                "high": [total] * 3,
                "position": [total] * 3,
                "lag": [0] * 3,
                "ready": 1,
                "desired": 1,
                "assignments": [[0, 1, 2]],
                "stable": True,
                "db_ms": 1,
            }
        )
    return {"id": identifier, "family": "ramp", "observations": rows}


def test_history_requires_contiguous_observations_and_resets_per_run():
    history = History()
    for row in trace()["observations"][:61]:
        history.append(row)
    assert len(history.features(0)) == 7
    history.append(trace("two")["observations"][0])
    with pytest.raises(ValueError, match="60 seconds"):
        history.features(0)


def test_horizon_labels_use_future_deltas_only_as_training_targets():
    run = trace()
    x, y, *_ = windows([run], 20)
    assert len(x) == len(y) and len(x) > 100
    changed = json.loads(json.dumps(run))
    changed["schedule"] = [[1, 999999]]
    changed["seed"] = 999
    assert (windows([changed], 20)[0] == x).all()
    history = History()
    for row in run["observations"][:61]:
        history.append(row)
    assert (x[0] == history.features(0)).all()


def test_whole_run_splits_and_artifact_integrity(tmp_path):
    with pytest.raises(ValueError, match="overlap"):
        train([trace()], [trace()])
    models, report = train([trace()], [trace("validation", 2)])
    assert len(models) == 12 and len(report) == 12
    identifier = publish(tmp_path, models, {"fingerprint": "test", "scores": report})
    assert load(tmp_path, "test")[0] == identifier
    with pytest.raises(ValueError, match="environment"):
        load(tmp_path, "changed")
    (tmp_path / f"{identifier}.pkl").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load(tmp_path)
