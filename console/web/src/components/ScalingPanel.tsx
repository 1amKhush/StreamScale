import { useState } from "react";
import type { Live } from "../api";
import { MODE_LABELS, TERMS } from "../analogy";
import { count, percent } from "../format";
import { useLens } from "../lens";
import { useOperation } from "../operations";

type Settings = {
  latency_target: number;
  min: number;
  max: number;
  cpu_target: number;
  lag_threshold: number;
  scale_down_seconds: number;
  polling_interval: number;
};

export type ScalingState = {
  mode: "fixed" | "manual" | "cpu-hpa" | "lag-keda" | "ml-predictive" | null;
  override: false | { replicas: number; since: number; base: string };
  predictive?: import("../types").PredictiveState;
  manual_replicas: number;
  settings: Settings;
  segments: {
    mode: string;
    override: boolean;
    from: number;
    to: number | null;
  }[];
  transition: {
    state: string;
    label?: string;
    step: string | null;
    error: string | null;
    at: string | null;
  };
  history: { at: number; label: string }[];
  ownership?: {
    found: string[];
    expected: string[];
    consistent: boolean;
    error?: string;
  };
};

const MODES = ["fixed", "manual", "cpu-hpa", "lag-keda", "ml-predictive"] as const;

function ReplicaButtons({
  value,
  onPick,
  disabled,
  testPrefix,
}: {
  value: number | null;
  onPick: (replicas: number) => void;
  disabled: boolean;
  testPrefix: string;
}) {
  return (
    <div className="replica-buttons" role="radiogroup" aria-label="Replicas">
      {[0, 1, 2, 3].map((replicas) => (
        <button
          key={replicas}
          role="radio"
          aria-checked={value === replicas}
          className={value === replicas ? "active" : ""}
          disabled={disabled}
          onClick={() => onPick(replicas)}
          data-testid={`${testPrefix}-${replicas}`}
        >
          {replicas}
        </button>
      ))}
    </div>
  );
}

export function ScalingPanel({ live }: { live: Live }) {
  const lens = useLens();
  const store = lens === "store";
  const operation = useOperation(live);
  const scaling = live.sample?.scaling as ScalingState | undefined;
  const point = live.points.at(-1);
  const [draft, setDraft] = useState<Settings | null>(null);
  if (!scaling?.mode)
    return (
      <p className="hint">
        Waiting for the scaling state from the cluster agent…
      </p>
    );
  const settings = draft ?? scaling.settings;
  const busy =
    scaling.transition.state === "running" ||
    [
      "scaling.mode",
      "scaling.replicas",
      "scaling.override",
      "scaling.release",
      "scaling.settings",
      "scaling.reapply",
    ].some(operation.busy);
  const autoscaled = scaling.mode === "cpu-hpa" || scaling.mode === "lag-keda" || scaling.mode === "ml-predictive";
  const override = scaling.override || null;
  const controllers = live.sample?.kubernetes.controllers ?? [];
  const error =
    operation.error ??
    [
      "scaling.mode",
      "scaling.replicas",
      "scaling.override",
      "scaling.release",
      "scaling.settings",
    ]
      .map(operation.failure)
      .find(Boolean);
  return (
    <div className="scaling-panel" data-testid="scaling-panel">
      <h3>{store ? "Floor manager's staffing rule" : "Scaling mode"}</h3>
      <div className="mode-list" role="radiogroup" aria-label="Scaling mode">
        {MODES.map((mode) => (
          <button
            key={mode}
            role="radio"
            aria-checked={scaling.mode === mode}
            className={`preset ${scaling.mode === mode ? "active" : ""}`}
            disabled={busy || scaling.mode === mode}
            onClick={() => operation.run("scaling.mode", { mode })}
            data-testid={`mode-${mode}`}
          >
            <strong>{MODE_LABELS[mode][lens]}</strong>
            <span>{MODE_LABELS[mode].explain}</span>
          </button>
        ))}
      </div>

      {scaling.mode === "ml-predictive" && (
        <div className="predictive-summary" data-testid="predictive-summary" role="status">
          <p><strong>ML Predictive</strong> · {override ? "manual override" : scaling.predictive?.status ?? "warming up"}</p>
          <p>{scaling.predictive?.reason ?? "Preparing observations from the pipeline."}</p>
          <dl>
            <dt>Observed → forecast</dt>
            <dd>{count(scaling.predictive?.observed_rate)} → {count(scaling.predictive?.forecast_rate)} events/s</dd>
            <dt>Look ahead</dt><dd>{count(scaling.predictive?.horizon)} seconds</dd>
          </dl>
          <p className="hint">Uses observed history only. The p95 target is an objective; sudden spikes and capacity limits can exceed it.</p>
        </div>
      )}
      <dl>
        <dt>
          {store
            ? "Wanted · present · at lanes"
            : "Desired · ready · group members"}
        </dt>
        <dd data-testid="scaling-counts">
          {count(point?.desired)} · {count(point?.ready)} ·{" "}
          {count(point?.members)}
        </dd>
        <dt>Controllers on the Deployment</dt>
        <dd data-testid="ownership">
          {scaling.ownership?.found?.length
            ? scaling.ownership.found.join(", ")
            : "none"}{" "}
          {scaling.ownership &&
            !scaling.ownership.error &&
            (scaling.ownership.consistent ? "✓ as expected" : "✕ unexpected")}
        </dd>
        <dt>Transition</dt>
        <dd data-testid="transition">
          {scaling.transition.state}
          {scaling.transition.state === "running" && scaling.transition.step
            ? `: ${scaling.transition.step}`
            : ""}
        </dd>
      </dl>
      {(scaling.transition.state === "failed" ||
        scaling.transition.state === "interrupted") && (
        <div className="confirm">
          <p>
            {scaling.transition.error} The last verified mode is{" "}
            <strong>{scaling.mode}</strong>.
          </p>
          <button
            disabled={busy}
            onClick={() => operation.run("scaling.reapply")}
          >
            Reapply {scaling.mode}
          </button>
        </div>
      )}

      {scaling.mode === "manual" && (
        <>
          <h4>{store ? "Cashiers on duty" : "Replicas"}</h4>
          <ReplicaButtons
            value={scaling.manual_replicas}
            disabled={busy}
            onPick={(replicas) =>
              operation.run("scaling.replicas", { replicas })
            }
            testPrefix="manual"
          />
          <p className="hint">
            {store
              ? "Zero sends every cashier on a break: queues grow, nothing is broken."
              : "Zero replicas stops processing; it is not a crash. Lag grows until replicas return."}
          </p>
        </>
      )}

      {autoscaled && (
        <>
          <h4>{store ? "Take over from the manager" : "Manual override"}</h4>
          {override ? (
            <div className="override-box" data-testid="override-active">
              <p>
                Override active since{" "}
                {new Date(override.since * 1000).toLocaleTimeString("en-GB")}:
                the{" "}
                {scaling.mode === "ml-predictive" ? "predictive loop" : scaling.mode === "cpu-hpa" ? "CPU HPA" : "KEDA ScaledObject"}{" "}
                is suspended while you hold {override.replicas}{" "}
                {store ? "cashiers" : "replicas"}.
              </p>
              <ReplicaButtons
                value={override.replicas}
                disabled={busy}
                onPick={(replicas) =>
                  operation.run("scaling.replicas", { replicas })
                }
                testPrefix="override"
              />
              <button
                className="primary"
                disabled={busy}
                onClick={() => operation.run("scaling.release")}
                data-testid="release-override"
              >
                Release override and restore{" "}
                {MODE_LABELS[scaling.mode as string][lens]}
              </button>
            </div>
          ) : (
            <>
              <p className="hint">
                Suspends the autoscaler first, then sets replicas directly, so
                two controllers never compete.
              </p>
              <ReplicaButtons
                value={null}
                disabled={busy}
                onPick={(replicas) =>
                  operation.run("scaling.override", { replicas })
                }
                testPrefix="override"
              />
            </>
          )}
          {scaling.mode === "ml-predictive" && (
            <label className="number-field">
              <span>p95 latency target (seconds)</span>
              <input type="number" min={1} max={30} value={settings.latency_target}
                onChange={(event) => setDraft({ ...settings, latency_target: Math.trunc(event.target.valueAsNumber) })} />
            </label>
          )}
          {scaling.mode === "ml-predictive" && draft && (
            <button disabled={busy} onClick={async () => {
              await operation.run("scaling.settings", { settings: draft }); setDraft(null);
            }}>Apply latency target</button>
          )}
          <details className="disclosure">
            <summary>
              Autoscaler settings{" "}
              <span>Targets, limits & scale-down delay</span>
            </summary>
            <div className="form-grid">
              {scaling.mode === "ml-predictive" ? (
                <p className="hint">Model {scaling.predictive?.model_id?.slice(0, 12) ?? "not prepared"}. Policy is prepared offline and frozen during a run.</p>
              ) : scaling.mode === "cpu-hpa" ? (
                <label className="number-field">
                  <span>CPU target (% of request)</span>
                  <input
                    type="number"
                    min={10}
                    max={400}
                    value={settings.cpu_target}
                    onChange={(event) =>
                      setDraft({
                        ...settings,
                        cpu_target: Math.trunc(event.target.valueAsNumber),
                      })
                    }
                  />
                </label>
              ) : (
                <label className="number-field">
                  <span>Lag threshold per {store ? "cashier" : "replica"}</span>
                  <input
                    type="number"
                    min={5}
                    max={10000}
                    value={settings.lag_threshold}
                    onChange={(event) =>
                      setDraft({
                        ...settings,
                        lag_threshold: Math.trunc(event.target.valueAsNumber),
                      })
                    }
                  />
                </label>
              )}
              {scaling.mode !== "ml-predictive" && (
              <label className="number-field">
                <span>Scale-down wait (s)</span>
                <input
                  type="number"
                  min={0}
                  max={600}
                  value={settings.scale_down_seconds}
                  onChange={(event) =>
                    setDraft({
                      ...settings,
                      scale_down_seconds: Math.trunc(
                        event.target.valueAsNumber,
                      ),
                    })
                  }
                />
              </label>
              )}
              <label className="number-field">
                <span>Minimum</span>
                <input
                  type="number"
                  min={1}
                  max={3}
                  value={settings.min}
                  onChange={(event) =>
                    setDraft({
                      ...settings,
                      min: Math.trunc(event.target.valueAsNumber),
                    })
                  }
                />
              </label>
              <label className="number-field">
                <span>Maximum (3 lanes)</span>
                <input
                  type="number"
                  min={1}
                  max={3}
                  value={settings.max}
                  onChange={(event) =>
                    setDraft({
                      ...settings,
                      max: Math.trunc(event.target.valueAsNumber),
                    })
                  }
                />
              </label>
            </div>
            <div className="button-row">
              <button
                disabled={busy || !draft}
                onClick={async () => {
                  if (!draft) return;
                  await operation.run("scaling.settings", { settings: draft });
                  setDraft(null);
                }}
              >
                Apply settings
              </button>
              {draft && <button onClick={() => setDraft(null)}>Discard</button>}
            </div>
          </details>
        </>
      )}

      <details className="disclosure">
        <summary>Controller details & history</summary>
        {controllers.map((controller) => (
          <div key={controller.name} className="controller">
            <h4>
              {controller.kind === "ScaledObject"
                ? TERMS.lagKeda[lens]
                : controller.owned_by_keda
                  ? "KEDA's HPA"
                  : TERMS.cpuHpa[lens]}
            </h4>
            {controller.kind === "HorizontalPodAutoscaler" &&
              !controller.owned_by_keda && (
                <p>
                  CPU now {percent(controller.cpu_utilization)} of request vs
                  target {percent(controller.cpu_target)} → wants{" "}
                  {count(controller.desired_replicas)}
                </p>
              )}
            {controller.owned_by_keda && (
              <p>
                KEDA wants {count(controller.desired_replicas)} replicas from
                the group's lag.
              </p>
            )}
            {controller.conditions
              ?.filter(
                (condition) =>
                  condition.type !== "ScalingLimited" ||
                  condition.status === "True",
              )
              .map((condition) => (
                <p key={condition.type} className="hint">
                  {condition.type}: {condition.reason} — {condition.message}
                </p>
              ))}
          </div>
        ))}

        <h4>Recent transitions</h4>
        <ul className="plain">
          {scaling.history
            .slice(-6)
            .reverse()
            .map((row) => (
              <li key={row.at}>
                {new Date(row.at * 1000).toLocaleTimeString("en-GB")} ·{" "}
                {row.label}
              </li>
            ))}
        </ul>
      </details>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
