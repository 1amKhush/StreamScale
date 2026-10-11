import { useEffect, useRef, useState } from "react";
import { newKey, submitOperation, type Live } from "./api";
import type { Operation } from "./types";

/**
 * One idempotency key per intent until its operation finishes: a double click, or a
 * click repeated while the first request is in flight, resubmits the same key and the
 * server returns the original operation instead of running it twice.
 */
export function useOperation(live: Live) {
  const inflight = useRef<Record<string, { kind: string; key: string; id?: string }>>({});
  const [, setVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<Record<string, string>>({});

  useEffect(() => {
    let changed = false;
    for (const [intent, entry] of Object.entries(inflight.current)) {
      const operation = live.operations.find((op: Operation) => op.id === entry.id);
      if (operation && operation.state !== "queued" && operation.state !== "running") {
        delete inflight.current[intent];
        changed = true;
      }
    }
    if (changed) setVersion((value) => value + 1);
  }, [live.operations]);

  const run = async (kind: string, params: Record<string, unknown> = {}) => {
    const intent = `${kind}:${JSON.stringify(params)}`;
    const entry = (inflight.current[intent] ??= { kind, key: newKey(kind.replace(".", "-")) });
    setVersion((value) => value + 1);
    setError(null);
    try {
      const { operation } = await submitOperation(kind, params, entry.key);
      entry.id = operation.id;
      setLast((value) => ({ ...value, [kind]: operation.id }));
      return operation;
    } catch (reason) {
      setError((reason as Error).message);
      delete inflight.current[intent];
      setVersion((value) => value + 1);
      return null;
    }
  };
  const busy = (kind: string) => Object.values(inflight.current).some((entry) => entry.kind === kind);
  const failure = (kind: string) => {
    const operation = live.operations.find((op: Operation) => op.id === last[kind]);
    return operation && (operation.state === "failed" || operation.state === "rejected") ? operation.error : null;
  };
  return { run, busy, failure, error };
}

/**
 * Controls that appear in place of the button just pressed ignore clicks briefly, so
 * the second click of a double click cannot land on, say, "Hold the doors".
 */
export function useArmed(signal: unknown, delayMs = 700) {
  const [armed, setArmed] = useState(true);
  useEffect(() => {
    setArmed(false);
    const timer = window.setTimeout(() => setArmed(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [signal, delayMs]);
  return armed;
}
