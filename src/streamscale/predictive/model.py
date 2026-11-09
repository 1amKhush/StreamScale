"""Quantile forecasts and integrity-checked, atomic local artifact publication."""

import hashlib
import importlib.metadata
import json
import os
import pickle
import tempfile
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss
from threadpoolctl import threadpool_limits

from streamscale.predictive import FEATURE_VERSION, HORIZONS, QUANTILES
from streamscale.predictive.data import windows
from streamscale.topics import MAX_REPLICAS, PARTITIONS


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def code_identity():
    return digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))})


def versions():
    return {name: importlib.metadata.version(name) for name in ("scikit-learn", "numpy", "scipy")}


def train(training, validation):
    def ids(runs):
        return {row["id"] for row in runs}

    if ids(training) & ids(validation):
        raise ValueError("Training/validation runs overlap")
    models, report = {}, {}
    with threadpool_limits(limits=1):
        for horizon in HORIZONS:
            x, y, _, _, _ = windows(training, horizon)
            vx, vy, persistence, trend, families = windows(validation, horizon)
            if len(x) < 30 or len(vx) < 15:
                raise ValueError("Insufficient contiguous training/validation observations")
            for quantile in QUANTILES:
                model = HistGradientBoostingRegressor(
                    loss="quantile",
                    quantile=quantile,
                    max_iter=80,
                    max_leaf_nodes=15,
                    min_samples_leaf=10,
                    random_state=42,
                    early_stopping=False,
                ).fit(x, y)
                models[horizon, quantile] = model
                predictions = np.maximum(0, model.predict(vx))
                scores = {}
                for family in sorted(set(families)):
                    mask = np.array([f == family for f in families])
                    scores[family] = {
                        name: float(
                            mean_pinball_loss(vy[mask], np.asarray(pred)[mask], alpha=quantile)
                        )
                        for name, pred in (
                            ("model", predictions),
                            ("persistence", persistence),
                            ("trend", trend),
                        )
                    }
                report[f"{horizon}:{quantile}"] = scores
    return models, report


def predict(models, history, horizon, quantile):
    features = [history.features(p) for p in range(PARTITIONS)]
    with threadpool_limits(limits=1):
        median = np.maximum(0, models[horizon, 0.5].predict(features))
        upper = np.maximum(median, models[horizon, quantile].predict(features))
    if not np.isfinite(median).all() or not np.isfinite(upper).all():
        raise ValueError("Non-finite forecast; use reactive fallback")
    return {"median": median.tolist(), "upper": upper.tolist()}


def publish(folder, models, metadata):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    payload = pickle.dumps(models, protocol=5)
    document = metadata | {
        "runtime_code": code_identity(),
        "feature_version": FEATURE_VERSION,
        "partitions": PARTITIONS,
        "replica_bounds": [1, MAX_REPLICAS],
        "versions": versions(),
        "model_sha256": hashlib.sha256(payload).hexdigest(),
    }
    identifier = digest(document)
    for name, data in (
        (f"{identifier}.pkl", payload),
        (f"{identifier}.json", json.dumps(document).encode()),
        ("current.json", json.dumps({"id": identifier}).encode()),
    ):
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            temporary = file.name
        os.replace(temporary, folder / name)
    return identifier


def load(folder, expected=None):
    folder = Path(folder)
    identifier = json.loads((folder / "current.json").read_text())["id"]
    if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid model identifier")
    document = json.loads((folder / f"{identifier}.json").read_text())
    payload = (folder / f"{identifier}.pkl").read_bytes()
    if (
        digest(document) != identifier
        or hashlib.sha256(payload).hexdigest() != document["model_sha256"]
    ):
        raise ValueError("Predictive artifact checksum mismatch")
    if (
        document["feature_version"] != FEATURE_VERSION
        or document.get("partitions") != PARTITIONS
        or document.get("replica_bounds") != [1, MAX_REPLICAS]
        or document["versions"] != versions()
        or document.get("runtime_code") != code_identity()
    ):
        raise ValueError("Incompatible predictive artifact")
    if expected is not None and document["fingerprint"] != expected:
        raise ValueError("Predictive artifact requires preparation for this environment")
    # Only locally prepared artifacts on the scoped runs PVC are accepted, never uploads.
    return identifier, pickle.loads(payload), document
