import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AgentStatus, ConsoleState, JournalEntry, Operation, Point, Sample, Workflow } from "./types";

const MAX_POINTS = 3600;

export type Connection = {
  socket: "connecting" | "open" | "closed";
  attempts: number;
  lastMessageAt: number | null;
  skew: number; // server clock minus browser clock, seconds
};

export type Live = {
  state: ConsoleState | null;
  points: Point[];
  sample: Sample | null;
  operations: Operation[];
  journal: JournalEntry[];
  status: AgentStatus | null;
  workflow: Workflow | null;
  connection: Connection;
  now: number; // server-aligned seconds
  extras: Record<string, unknown>;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, body.error ?? response.statusText);
  return body as T;
}

export async function submitOperation(kind: string, params: Record<string, unknown>, key: string) {
  const response = await fetch("/api/operations", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, params, key }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, body.error ?? response.statusText);
  return body as { operation: Operation; created: boolean };
}

export function newKey(prefix: string) {
  const random = crypto.getRandomValues(new Uint32Array(2));
  return `${prefix}-${Date.now().toString(36)}-${random[0].toString(36)}${random[1].toString(36)}`;
}

const COMPONENT_KEYS = ["server", "status", "points", "sample", "journal", "operations", "workflow", "now"];

export function useConsole(): Live {
  const [state, setState] = useState<ConsoleState | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [sample, setSample] = useState<Sample | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [journal, setJournal] = useState<JournalEntry[]>([]);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [extras, setExtras] = useState<Record<string, unknown>>({});
  const [connection, setConnection] = useState<Connection>({
    socket: "connecting",
    attempts: 0,
    lastMessageAt: null,
    skew: 0,
  });
  const [clock, setClock] = useState(Date.now() / 1000);
  const attempts = useRef(0);
  const skew = useRef(0);

  const applyState = useCallback((value: ConsoleState) => {
    setState(value);
    setPoints(value.points ?? []);
    setSample(value.sample ?? null);
    setOperations(value.operations ?? []);
    setJournal(value.journal ?? []);
    setStatus(value.status ?? null);
    setWorkflow(value.workflow ?? null);
    const component: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      if (!COMPONENT_KEYS.includes(key)) component[key] = item;
    }
    setExtras(component);
    skew.current = value.now - Date.now() / 1000;
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;

    const connect = () => {
      setConnection((value) => ({ ...value, socket: "connecting", attempts: attempts.current }));
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${location.host}/api/stream`);
      socket.onopen = () => {
        attempts.current = 0;
        setConnection((value) => ({ ...value, socket: "open", attempts: 0 }));
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        const received = Date.now() / 1000;
        setConnection((value) => ({ ...value, lastMessageAt: received, skew: skew.current }));
        switch (message.type) {
          case "hello":
            applyState(message.state);
            break;
          case "tick":
            setPoints((current) => {
              const next = current.length >= MAX_POINTS ? current.slice(-MAX_POINTS + 1) : current.slice();
              next.push(message.point);
              return next;
            });
            setSample(message.sample);
            break;
          case "operation":
            setOperations((current) => {
              const others = current.filter((op) => op.id !== message.operation.id);
              return [...others, message.operation].slice(-60);
            });
            break;
          case "journal":
            setJournal((current) => [...current, message.entry].slice(-400));
            break;
          case "status":
            setStatus(message.status);
            break;
          case "server":
            skew.current = message.now - received;
            setWorkflow(message.workflow);
            break;
          case "component":
            setExtras((current) => ({ ...current, [message.name]: message.value }));
            break;
          case "resync":
            getJson<ConsoleState>("/api/state").then(applyState).catch(() => undefined);
            break;
        }
      };
      socket.onclose = () => {
        if (closed) return;
        attempts.current += 1;
        setConnection((value) => ({ ...value, socket: "closed", attempts: attempts.current }));
        const delay = Math.min(1000 * 2 ** Math.min(attempts.current - 1, 3), 10000);
        retry = window.setTimeout(connect, delay);
      };
    };
    connect();
    const timer = window.setInterval(() => setClock(Date.now() / 1000), 1000);
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(timer);
      socket?.close();
    };
  }, [applyState]);

  const now = clock + connection.skew;
  return useMemo(
    () => ({ state, points, sample, operations, journal, status, workflow, connection, now, extras }),
    [state, points, sample, operations, journal, status, workflow, connection, now, extras],
  );
}

export type Freshness = "live" | "stale" | "disconnected" | "agent-unreachable" | "waiting";

/** One overall freshness verdict; data is never presented as live when it is not. */
export function freshness(live: Live, staleAfter = 3.5): { verdict: Freshness; age: number | null } {
  const last = live.points.at(-1);
  const age = last ? Math.max(0, live.now - last.received_at) : null;
  if (live.connection.socket !== "open") return { verdict: "disconnected", age };
  if (live.status?.agent === "unreachable") return { verdict: "agent-unreachable", age };
  if (!last) return { verdict: "waiting", age };
  if (age !== null && age > staleAfter) return { verdict: "stale", age };
  return { verdict: "live", age };
}
