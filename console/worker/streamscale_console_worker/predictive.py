"""Predictive loop owned by Scaling's actuator lock, never by the host/browser."""

import time

from streamscale.predictive import DEFAULT_POLICY
from streamscale.predictive.data import History, observation
from streamscale.predictive.model import load, predict
from streamscale.predictive.policy import recommend


class Predictive:
    def __init__(self, scaling):
        self.scaling = scaling
        self.history = History()
        self.artifact = None
        self.error = None
        self.low_since = None
        self.pending = None
        self.last_decision = 0
        self.state = {"status": "warming up", "reason": "Waiting for observations"}

    def reset(self):
        self.state = {
            "status": "warming up",
            "reason": "Collecting 60 seconds of current run history",
        }
        self.artifact = None
        self.last_decision = 0
        self.history = History()
        self.low_since = None
        self.pending = None

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick(time.time())
            except Exception as error:  # noqa: BLE001 - hold replicas, expose error, retry
                self.history = History()
                self.low_since = None
                self.state = {
                    "status": "fallback",
                    "reason": f"Controller holding: {type(error).__name__}: {error}",
                }
            stop.wait(1)

    def tick(self, now):
        scaling = self.scaling
        if not scaling.work.acquire(blocking=False):
            return
        try:
            if scaling.state["mode"] != "ml-predictive" or scaling.state["override"]:
                self.reset()
                self.state = {
                    "status": "manual override" if scaling.state["override"] else "inactive"
                }
                return
            if scaling.state["transition"]["state"] not in {"succeeded", "idle"}:
                self.state = {
                    "status": "fallback",
                    "reason": "Reapply interrupted scaling transition",
                }
                return
            sample = scaling.agent.observer.latest()
            if not sample or not 0 <= now - sample["sampled_at"] <= 5:
                raise ValueError("Stale observations; holding replicas")
            row = observation(sample)
            if self.history.rows and row["t"] == self.history.rows[-1]["t"]:
                return
            if self.history.rows and (
                row["run_id"] != self.history.rows[-1]["run_id"]
                or row["t"] - self.history.rows[-1]["t"] > 5
            ):
                self.reset()
            self.history.append(row)
            if len(self.history.rows) < 2 or now - self.last_decision < 5:
                return
            self.last_decision = now
            try:
                if self.artifact is None:
                    self.artifact = load(scaling.root / "predictive")
                identifier, models, metadata = self.artifact
            except (OSError, ValueError, KeyError) as error:
                self.artifact = None
                self.state = {"status": "fallback", "reason": f"Prepare model: {error}"}
                return  # no uncalibrated capacity guesses
            policy = metadata.get("policy", DEFAULT_POLICY)
            forecast = None
            status = "predicting"
            reason = None
            try:
                forecast = predict(models, self.history, policy["horizon"], policy["quantile"])
            except Exception as error:  # noqa: BLE001 - internal reactive fallback
                status = (
                    "warming up"
                    if self.history.rows[-1]["t"] - self.history.rows[0]["t"] < 60
                    else "fallback"
                )
                reason = str(error)
            rates = self.history.rates()
            result = recommend(
                row,
                rates,
                metadata["calibration"],
                policy,
                forecast,
                scaling.state["settings"]["latency_target"],
                scaling.state["settings"]["min"],
                scaling.state["settings"]["max"],
            )
            if forecast and result["capacity_limited"]:
                status = "capacity limited"
            self.state = result | {
                "status": status,
                "reason": reason or result["reason"],
                "at": now,
                "run_id": row["run_id"],
                "observed_rate": sum(rates),
                "forecast_rate": sum(forecast["upper"]) / policy["horizon"] if forecast else None,
                "forecast_median_rate": sum(forecast["median"]) / policy["horizon"]
                if forecast
                else None,
                "forecast_from": row["t"],
                "forecast_until": row["t"] + policy["horizon"],
                "horizon": policy["horizon"],
                "model_id": identifier,
                "policy": policy,
                "target_seconds": scaling.state["settings"]["latency_target"],
                "desired": row["desired"],
                "ready": row["ready"],
                "applied": False,
            }
            # Ownership is rechecked immediately before every possible write.
            from streamscale_console_worker.scaling import owners

            if owners(*scaling.kube.autoscalers()):
                raise ValueError("Unexpected external controller; holding replicas")
            desired = result["recommended"]
            if self.pending is not None:
                if row["ready"] != self.pending or not row["stable"]:
                    self.state["reason"] = "Waiting for ready consumers and stable assignments"
                    return
                self.pending = None
            if not row["stable"] or row["ready"] != row["desired"]:
                return
            if desired < row["desired"]:
                if self.low_since is None:
                    self.low_since = now
                if now - self.low_since < policy["downscale"]:
                    self.state["reason"] = "Holding capacity during scale-down stabilization"
                    return
                desired = max(desired, row["desired"] - 1)
            else:
                self.low_since = None
            if desired != row["desired"]:
                scaling.kube.scale_consumers(desired)
                self.pending = desired
                self.low_since = None
                self.state["applied"] = True
                self.state["desired"] = desired
        finally:
            scaling.work.release()
