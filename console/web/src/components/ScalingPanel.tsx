import { useState, type KeyboardEvent } from "react";
import { MAX_REPLICAS } from "../capacity";
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
const MODE_NAMES: Record<(typeof MODES)[number], string> = {
  fixed: "Fixed",
  manual: "Manual",
  "cpu-hpa": "CPU autoscaling",
  "lag-keda": "Queue autoscaling",
  "ml-predictive": "ML Predictive",
};
const MODE_HINTS: Record<(typeof MODES)[number], string> = {
  fixed: "Always 1 consumer",
  manual: "You choose 0–12",
  "cpu-hpa": "Respond to CPU use",
  "lag-keda": "Respond to backlog",
  "ml-predictive": "Anticipate traffic",
};

function moveRadio(event: KeyboardEvent<HTMLDivElement>) {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  const choices = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="radio"]:not(:disabled)')];
  if (!choices.length) return;
  const current = choices.indexOf(document.activeElement as HTMLButtonElement);
  const next = event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1 : (current + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) + choices.length) % choices.length;
  event.preventDefault();
  choices[next].focus();
  choices[next].click();
}

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
    <div className="replica-buttons" role="radiogroup" aria-label="Replicas" onKeyDown={moveRadio}>
      {Array.from({ length: MAX_REPLICAS + 1 }, (_, n) => n).map((replicas) => (
        <button
          key={replicas}
          role="radio"
          aria-checked={value === replicas}
          tabIndex={value === replicas || (value === null && replicas === 0) ? 0 : -1}
          className={value === replicas ? "active" : ""}
          disabled={disabled}
          onClick={() => onPick(replicas)}
          data-testid={`${testPrefix}-${replicas}`}
        >
          {replicas}
          {replicas === 0 && <span className="control-replica-caption">Pause</span>}
        </button>
      ))}
    </div>
  );
}

export function ScalingPanel({ live, onDocs }: { live: Live; onDocs?: () => void }) {
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
  const predictive = scaling.predictive;
  const modelUnavailable =
    scaling.mode === "ml-predictive" &&
    predictive?.status === "fallback" &&
    predictive.reason?.startsWith("Prepare model:");
  const settingsValid =
    Number.isInteger(settings.min) && settings.min >= 1 && settings.min <= MAX_REPLICAS &&
    Number.isInteger(settings.max) && settings.max >= settings.min && settings.max <= MAX_REPLICAS &&
    Number.isInteger(settings.latency_target) && settings.latency_target >= 1 && settings.latency_target <= 30 &&
    Number.isInteger(settings.cpu_target) && settings.cpu_target >= 10 && settings.cpu_target <= 400 &&
    Number.isInteger(settings.lag_threshold) && settings.lag_threshold >= 5 && settings.lag_threshold <= 10000 &&
    Number.isInteger(settings.scale_down_seconds) && settings.scale_down_seconds >= 0 && settings.scale_down_seconds <= 600;
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
      <h3>{store ? "How should capacity adjust?" : "Choose a scaling mode"}</h3>
      <p className="control-help">Choose one rule. You can change it during a sale or take over manually.</p>
      <div className="mode-list" role="radiogroup" aria-label="Scaling mode" onKeyDown={moveRadio}>
        {MODES.map((mode) => (
          <button
            key={mode}
            role="radio"
            aria-checked={scaling.mode === mode}
            tabIndex={scaling.mode === mode ? 0 : -1}
            className={`preset ${scaling.mode === mode ? "active" : ""}`}
            disabled={busy}
            onClick={() => {
              if (scaling.mode === mode) return;
              setDraft(null);
              operation.run("scaling.mode", { mode });
            }}
            data-testid={`mode-${mode}`}
          >
            <strong>{MODE_NAMES[mode]}</strong>
            <span>{MODE_HINTS[mode]}</span>
          </button>
        ))}
      </div>
      <p className="control-selection-description">{MODE_LABELS[scaling.mode].explain}</p>

      <div className="control-metrics" data-testid="scaling-counts" aria-label="Consumer capacity">
        <div className="control-metric"><span>Requested</span><strong>{count(point?.desired)}</strong></div>
        <div className="control-metric"><span>Ready to process</span><strong>{count(point?.ready)}</strong></div>
        <div className="control-metric"><span>Joined the group</span><strong>{count(point?.members)}</strong></div>
      </div>
      <div className="control-status">
        <span data-testid="ownership">External controllers: {scaling.ownership?.found?.length ? scaling.ownership.found.join(", ") : "none"}{" "}{scaling.ownership && !scaling.ownership.error && (scaling.ownership.consistent ? "✓ as expected" : "✕ unexpected")}{scaling.ownership?.error && ` · ${scaling.ownership.error}`}</span>
        <span>Mode change: <span data-testid="transition">{scaling.transition.state}{scaling.transition.state === "running" && scaling.transition.step ? `: ${scaling.transition.step}` : ""}</span></span>
      </div>

      {scaling.mode === "ml-predictive" && (
        <div className="predictive-summary" data-testid="predictive-summary" role="status">
          <p><strong>ML Predictive</strong><span className="control-status-label">{override ? "Manual override" : modelUnavailable ? "Preparation needed" : predictive?.status ?? "Warming up"}</span></p>
          {override ? (
            <p>You are choosing capacity. Release the override to resume prediction.</p>
          ) : modelUnavailable ? (
            <div className="control-warning" data-testid="predictive-prepare-help">
              <p>The trained model is not ready. Current replicas are held; predictive scaling has not started.</p>
              <p>From the project folder, run <code>./streamscale.sh predictive-prepare</code>. Preparation collects training runs and tunes a reusable policy.</p>
              {onDocs && <button className="link-button" onClick={onDocs}>Read the ML Predictive guide</button>}
            </div>
          ) : (
            <p>{predictive?.reason ?? "Collecting the first 60 seconds of current traffic history."}</p>
          )}
          {!modelUnavailable && (
            <dl>
              <dt>Traffic now</dt><dd>{count(predictive?.observed_rate)} events/s</dd>
              <dt>Traffic forecast</dt><dd>{count(predictive?.forecast_rate)} events/s · next {count(predictive?.horizon)} seconds</dd>
            </dl>
          )}
          <p className="hint">Learns from observed history. The latency target is an objective; sudden spikes or full capacity can exceed it.</p>
        </div>
      )}
      {scaling.ownership && !scaling.ownership.error && !scaling.ownership.consistent && (
        <p className="error-text" role="alert">Unexpected scaling controller detected. Inspect controller details before continuing.</p>
      )}
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
            Retry {MODE_NAMES[scaling.mode]}
          </button>
        </div>
      )}

      {scaling.mode === "manual" && (
        <>
          <h4>{store ? "How many cashiers?" : "How many consumer replicas?"}</h4>
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
          {scaling.mode === "ml-predictive" && (
            <div className="scaling-target">
              <label className="number-field">
                <span>p95 latency target (seconds)</span>
                <input type="number" min={1} max={30} value={Number.isFinite(settings.latency_target) ? settings.latency_target : ""}
                  disabled={busy}
                  onChange={(event) => setDraft({ ...settings, latency_target: Math.trunc(event.target.valueAsNumber) })} />
              </label>
              <p className="hint">Aim to commit 95% of events within this time.</p>
              {draft && draft.latency_target !== scaling.settings.latency_target && (
                <button disabled={busy || !settingsValid} onClick={async () => {
                  const submitted = await operation.run("scaling.settings", { settings: draft });
                  if (submitted) setDraft(null);
                }}>Apply latency target</button>
              )}
              {draft && (!Number.isInteger(settings.latency_target) || settings.latency_target < 1 || settings.latency_target > 30) && <p className="error-text" role="alert">Choose a whole number from 1 to 30 seconds.</p>}
            </div>
          )}
          <h4>Manual override</h4>
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
                Resume {MODE_NAMES[scaling.mode]}
              </button>
            </div>
          ) : (
            <>
              <p className="hint">
                Choose a replica count to pause automatic scaling and take control. Zero pauses processing while the queue grows.
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
          <details className="disclosure">
            <summary>
              Advanced scaling settings{" "}
              <span>Replica limits, targets & policy details</span>
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
                <span>Minimum replicas</span>
                <input
                  type="number"
                  min={1}
                  max={MAX_REPLICAS}
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
                <span>Maximum replicas (12 partitions)</span>
                <input
                  type="number"
                  min={1}
                  max={MAX_REPLICAS}
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
                disabled={busy || !draft || !settingsValid}
                onClick={async () => {
                  if (!draft) return;
                  const submitted = await operation.run("scaling.settings", { settings: draft });
                  if (submitted) setDraft(null);
                }}
              >
                Apply settings
              </button>
              {draft && <button onClick={() => setDraft(null)}>Discard</button>}
            </div>
            {draft && !settingsValid && <p className="error-text" role="alert">Use values within the displayed limits. Minimum replicas cannot exceed maximum replicas.</p>}
          </details>
        </>
      )}

      <details className="disclosure">
        <summary>Controller details & history</summary>
        {scaling.mode === "ml-predictive" && modelUnavailable && <p>{predictive?.reason}</p>}
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
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  );
}
