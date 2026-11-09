import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  applyNodeChanges,
  Handle,
  Position,
  ReactFlow,
  Background,
  Controls,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { MODE_LABELS, TERMS, type Lens } from "../analogy";
import { count, percent, rate, seconds, shortPod, bytes } from "../format";
import { particleCount, type CashierModel, type FloorModel, type LaneModel, type Severity } from "../graph";
import { useLens } from "../lens";

type Data = Record<string, unknown>;

const ROW_SPACING = 140;
const ALERT_TEXT: Record<string, [string, string]> = {
  lane: ["Queue building", "Lag growing"],
  cashier: ["At capacity", "Saturated"],
  ledger: ["Slow", "Slow"],
  doors: ["Blocked", "Blocked"],
  desk: ["Busy", "Busy"],
};

function word(id: keyof typeof TERMS, lens: Lens) {
  return TERMS[id][lens];
}

function NodeFrame({
  kind,
  title,
  caption,
  alert,
  selected,
  icon,
  children,
  testId,
  tooltip,
}: {
  kind: string;
  title: string;
  caption: string;
  alert: Severity;
  selected: boolean;
  icon: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  tooltip?: string;
}) {
  const lens = useLens();
  return (
    <div
      className={`floor-node floor-${kind} ${alert ? `alert-${alert}` : ""} ${selected ? "is-selected" : ""}`}
      data-testid={testId}
      title={tooltip}
    >
      <header>
        <span className="node-icon" aria-hidden>
          {icon}
        </span>
        <span className="term">
          <span className="term-main">{title}</span>
          <span className="term-caption">{caption}</span>
        </span>
        {alert && (
          <span className={`alert-tag alert-tag-${alert}`}>
            {alert === "critical" ? "⛔ " : "⚠ "}
            {alert === "critical" && !kind.startsWith("cashier")
              ? "Unavailable"
              : (ALERT_TEXT[kind.split(" ")[0]] ?? ["Attention", "Attention"])[lens === "store" ? 0 : 1]}
          </span>
        )}
      </header>
      {children}
    </div>
  );
}

const DoorIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20">
    <rect x="4" y="3" width="16" height="18" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M12 3v18" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="10" cy="12" r="1" fill="currentColor" />
    <circle cx="14" cy="12" r="1" fill="currentColor" />
  </svg>
);
const LaneIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20">
    <rect x="2" y="9" width="20" height="6" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="7" cy="12" r="1.4" fill="currentColor" />
    <circle cx="12" cy="12" r="1.4" fill="currentColor" />
    <circle cx="17" cy="12" r="1.4" fill="currentColor" />
  </svg>
);
const PersonIcon = () => (
  <svg viewBox="0 0 24 24" width="22" height="22">
    <circle cx="12" cy="7.5" r="3.6" fill="currentColor" />
    <path d="M4.5 21c.6-4.6 3.6-7.2 7.5-7.2s6.9 2.6 7.5 7.2z" fill="currentColor" />
  </svg>
);
const ClipboardIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20">
    <rect x="5" y="4" width="14" height="17" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M9 4h6v3H9zM8 11h8M8 15h6" stroke="currentColor" strokeWidth="1.6" fill="none" />
  </svg>
);
const LedgerIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20">
    <path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M9 9h6M9 13h6" stroke="currentColor" strokeWidth="1.6" />
  </svg>
);
const DeskIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20">
    <path d="M3 13l3-8h12l3 8v6H3z" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M3 13h5l1.5 2.5h5L16 13h5" fill="none" stroke="currentColor" strokeWidth="1.6" />
  </svg>
);

const DoorsNode = memo(function DoorsNode({ data, selected }: NodeProps<Node<Data>>) {
  const lens = useLens();
  const doors = data.doors as FloorModel["doors"];
  const progress = doors.budget ? Math.min(1, (doors.acknowledged ?? 0) / doors.budget) : 0;
  const stateText: Record<string, string> =
    lens === "store"
      ? { idle: "Closed: no sale", preparing: "Opening…", publishing: "Open: shoppers arriving", paused: "Doors held: no new shoppers", stopping: "Closing…", finished: "Sale over", failed: "Sale failed" }
      : { idle: "Idle", preparing: "Preparing", publishing: "Publishing", paused: "Paused", stopping: "Flushing", finished: "Published", failed: "Failed" };
  return (
    <NodeFrame kind="doors" title={word("publisher", lens)} caption={word("publisher", lens === "store" ? "system" : "store")} alert={doors.alert} selected={selected} icon={<DoorIcon />} testId="node-doors">
      <p className="node-state">{stateText[doors.state] ?? doors.state}</p>
      <div className="node-figure">
        <strong>{rate(doors.arrival)}</strong>
        <span>{lens === "store" ? "arriving" : "acknowledged into Kafka"}</span>
      </div>
      {doors.budget ? (
        <div className="meter" aria-label="Sale progress">
          <span style={{ width: `${progress * 100}%` }} />
        </div>
      ) : null}
      <dl className="node-dl">
        <dt>{lens === "store" ? "Shoppers in" : "Acknowledged"}</dt>
        <dd>
          {count(doors.acknowledged)} / {count(doors.budget)}
        </dd>
        {doors.targetRate !== null && doors.state === "publishing" && (
          <>
            <dt>{lens === "store" ? "Planned rate" : "Target rate"}</dt>
            <dd>{rate(doors.targetRate)}</dd>
          </>
        )}
        {!!doors.failed && (
          <>
            <dt>Delivery failures</dt>
            <dd className="bad">{count(doors.failed)}</dd>
          </>
        )}
      </dl>
      {doors.bufferFull && <p className="node-warning">⚠ Broker not accepting: publisher is buffering</p>}
      <Handle type="source" position={Position.Right} />
    </NodeFrame>
  );
});

function QueueBelt({ lane, scale }: { lane: LaneModel; scale: number }) {
  const fill = lane.lag ? Math.min(1, lane.lag / scale) : 0;
  const dots = lane.lag ? Math.min(14, Math.max(1, Math.round(fill * 14))) : 0;
  return (
    <div className={`belt lane-${lane.partition}`} role="img" aria-label={`Queue length ${lane.lag ?? "unknown"}`}>
      <span className="belt-fill" style={{ width: `${fill * 100}%` }} />
      <span className="belt-dots">
        {Array.from({ length: dots }, (_, index) => (
          <i key={index} />
        ))}
      </span>
    </div>
  );
}

const LaneNode = memo(function LaneNode({ data, selected }: NodeProps<Node<Data>>) {
  const lens = useLens();
  const lane = data.lane as LaneModel;
  const scale = data.scale as number;
  const arrow = lane.trend === "growing" ? "▲ growing" : lane.trend === "shrinking" ? "▼ shrinking" : lane.trend === "steady" ? "■ steady" : "";
  const name = lens === "store" ? `Lane ${lane.partition}` : `Partition ${lane.partition}`;
  return (
    <NodeFrame
      kind={`lane lane-key-${lane.partition}`}
      title={name}
      caption={lens === "store" ? `partition ${lane.partition}` : `checkout lane ${lane.partition}`}
      alert={lane.alert && lane.trend === "growing" ? lane.alert : null}
      selected={selected}
      icon={<LaneIcon />}
      testId={`node-lane-${lane.partition}`}
    >
      <Handle type="target" position={Position.Left} />
      <QueueBelt lane={lane} scale={scale} />
      <div className="node-figure">
        <strong data-testid={`lane-${lane.partition}-lag`}>{count(lane.lag)}</strong>
        <span>
          {word("lag", lens).toLowerCase()} {arrow && <em className={`trend trend-${lane.trend}`}>{arrow}</em>}
        </span>
      </div>
      <dl className="node-dl">
        <dt>{lens === "store" ? "Cashier" : "Owner"}</dt>
        <dd title={lane.owner ?? undefined}>{lane.owner ? (lane.ownerKnown ? shortPod(lane.owner) : lane.owner) : <span className="bad">none</span>}</dd>
      </dl>
      <Handle type="source" position={Position.Right} />
    </NodeFrame>
  );
});

const STATUS_TEXT: Record<CashierModel["status"], [string, string]> = {
  serving: ["Serving", "Consuming"],
  standby: ["On standby", "No partition"],
  waiting: ["Waiting for a sale", "Idle"],
  retrying: ["Retrying", "Retrying"],
  arriving: ["Arriving", "Starting"],
  leaving: ["Leaving", "Terminating"],
  collapsed: ["Collapsed", "Crashed"],
  overwhelmed: ["Overwhelmed", "OOMKilled"],
};

const CashierNode = memo(function CashierNode({ data, selected }: NodeProps<Node<Data>>) {
  const lens = useLens();
  const cashier = data.cashier as CashierModel;
  const target = data.cpuTarget as number | null;
  const busy = cashier.cpuRequestPct;
  const meter = busy === null ? 0 : Math.min(1, busy / 200);
  return (
    <NodeFrame
      kind={`cashier status-${cashier.status}`}
      title={`${lens === "store" ? "Cashier" : "Consumer"} ${shortPod(cashier.name)}`}
      caption={lens === "store" ? "Kafka consumer" : "Cashier"}
      alert={cashier.alert}
      selected={selected}
      icon={<PersonIcon />}
      testId={`node-cashier-${shortPod(cashier.name)}`}
      tooltip={cashier.name}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="target" position={Position.Top} id="control" />
      <p className={`node-state status-text-${cashier.status}`} title={cashier.detail}>
        {STATUS_TEXT[cashier.status][lens === "store" ? 0 : 1]}
      </p>
      <div className="lane-chips">
        {cashier.lanes.length ? (
          cashier.lanes.map((lane) => (
            <span key={lane} className={`lane-chip lane-key-${lane}`}>
              {lens === "store" ? `Lane ${lane}` : `P${lane}`}
            </span>
          ))
        ) : (
          <span className="muted">{lens === "store" ? "No lane" : "No partitions"}</span>
        )}
      </div>
      <div className="cpu-meter" title="CPU use relative to the pod's request (the CPU HPA's basis); the marker is the HPA target">
        <span className="cpu-fill" style={{ width: `${meter * 100}%` }} />
        {target !== null && <span className="cpu-target" style={{ left: `${Math.min(1, target / 200) * 100}%` }} />}
      </div>
      <p className="node-small" title={`CPU ${percent(busy)} of request · ${percent(cashier.cpuLimitPct)} of limit · ${bytes(cashier.rssBytes)} memory`}>
        CPU {percent(busy)} of request
        {cashier.restarts ? ` · ${cashier.restarts} restart${cashier.restarts > 1 ? "s" : ""}` : ""}
      </p>
      {cashier.faults.length > 0 && <p className="node-warning" title={cashier.faults.join(", ")}>{cashier.faults.length} {cashier.faults.length === 1 ? "fault" : "faults"} armed</p>}
      <Handle type="source" position={Position.Right} />
    </NodeFrame>
  );
});

const ManagerNode = memo(function ManagerNode({ data, selected }: NodeProps<Node<Data>>) {
  const lens = useLens();
  const manager = data.manager as FloorModel["manager"];
  const label = MODE_LABELS[manager.mode] ?? { store: manager.mode, system: manager.mode, explain: "" };
  const modeText = label[lens].replace("–12", `–${data.partitionCount}`);
  const hpa = manager.controllers.find((row) => row.kind === "HorizontalPodAutoscaler");
  const keda = manager.controllers.find((row) => row.kind === "ScaledObject");
  return (
    <NodeFrame kind="manager" title={word("controller", lens)} caption={word("controller", lens === "store" ? "system" : "store")} alert={null} selected={selected} icon={<ClipboardIcon />} testId="node-manager"
      tooltip={hpa && !hpa.owned_by_keda ? `CPU ${percent(hpa.cpu_utilization)} / ${percent(hpa.cpu_target)} target` : keda ? `Lag threshold per consumer: ${keda.lag_threshold ?? "—"}` : label.explain}>
      <p className="node-state" title={label.explain}>
        {modeText}
        {manager.override && <strong className="override"> · manual override</strong>}
      </p>
      <dl className="node-dl">
        <dt>{lens === "store" ? "Wanted · present · at lanes" : "Desired · ready · members"}</dt>
        <dd data-testid="replica-counts">
          {count(manager.desired)} · {count(manager.ready)} · {count(manager.members)}
        </dd>
      </dl>
      <Handle type="source" position={Position.Bottom} />
    </NodeFrame>
  );
});

const LedgerNode = memo(function LedgerNode({ data, selected }: NodeProps<Node<Data>>) {
  const lens = useLens();
  const ledger = data.ledger as FloorModel["ledger"];
  const statuses = ["CREATED", "PAID", "CANCELLED"];
  const total = statuses.reduce((sum, key) => sum + (ledger.statusCounts[key] ?? 0), 0);
  return (
    <NodeFrame kind="ledger" title={word("database", lens)} caption={word("database", lens === "store" ? "system" : "store")} alert={ledger.alert} selected={selected} icon={<LedgerIcon />} testId="node-ledger">
      <Handle type="target" position={Position.Left} />
      {ledger.available === false && <p className="node-warning">⛔ PostgreSQL unavailable</p>}
      <div className="node-figure">
        <strong data-testid="ledger-facts">{count(ledger.facts)}</strong>
        <span>{lens === "store" ? "events recorded" : "order_event facts"}</span>
      </div>
      <dl className="node-dl">
        <dt>{lens === "store" ? "Recorded per second" : "Facts/s"}</dt>
        <dd>{rate(ledger.storedRate)}</dd>
        <dt>{lens === "store" ? "Orders on the board" : "order_state rows"}</dt>
        <dd>{count(ledger.states)}</dd>
        <dt>DB p95 (live est.)</dt>
        <dd>{seconds(ledger.dbP95)}</dd>
      </dl>
      {total > 0 && (
        <div className="status-board" aria-label="Order status board">
          {statuses.map((key) => (
            <span key={key} className={`status-cell status-${key.toLowerCase()}`} style={{ flexGrow: ledger.statusCounts[key] ?? 0 }} title={`${key}: ${count(ledger.statusCounts[key])}`}>
              {key === "PAID" ? "Paid" : key === "CANCELLED" ? "Cancelled" : "Created"} {count(ledger.statusCounts[key])}
            </span>
          ))}
        </div>
      )}
    </NodeFrame>
  );
});

export const CODE_TEXT: Record<string, string> = {
  INVALID_JSON: "unreadable label",
  INVALID_ENCODING: "smudged label",
  SCHEMA_VALIDATION_FAILED: "missing fields",
  UNSUPPORTED_SCHEMA_VERSION: "unknown form version",
  UNSUPPORTED_EVENT_TYPE: "unknown kind of slip",
  INVALID_AMOUNT: "impossible price",
  INVALID_TIMESTAMP: "bad date",
  KEY_MISMATCH: "wrong loyalty card",
};

const DeskNode = memo(function DeskNode({ data, selected }: NodeProps<Node<Data>>) {
  const lens = useLens();
  const desk = data.desk as FloorModel["desk"];
  const codes = Object.entries(desk.codes).sort((a, b) => b[1] - a[1]);
  return (
    <NodeFrame kind="desk" title={word("dlq", lens)} caption={word("dlq", lens === "store" ? "system" : "store")} alert={desk.alert} selected={selected} icon={<DeskIcon />} testId="node-desk">
      <Handle type="target" position={Position.Left} />
      <div className="node-figure">
        <strong data-testid="desk-copies">{count(desk.copies)}</strong>
        <span>{lens === "store" ? "problem items" : "DLQ copies this run"}</span>
      </div>
      <ul className="code-list">
        {codes.slice(0, 4).map(([code, value]) => (
          <li key={code}>
            <span>{lens === "store" ? (CODE_TEXT[code] ?? code) : code}</span>
            <span>{count(value)}</span>
          </li>
        ))}
      </ul>
    </NodeFrame>
  );
});

const FlowEdge = memo(function FlowEdge(props: EdgeProps<Edge<Data>>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, id } = props;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const edgeRate = (data?.rate as number | null) ?? null;
  const kind = data?.kind as string;
  const lane = data?.lane as number | undefined;
  const reduced = !!data?.reducedMotion;
  const particles = kind === "control" ? 0 : particleCount(edgeRate, reduced);
  const className = `edge edge-${kind} ${lane !== undefined ? `lane-stroke-${lane}` : ""} ${edgeRate ? "is-active" : ""}`;
  return (
    <>
      <BaseEdge id={id} path={path} className={className} />
      {Array.from({ length: particles }, (_, index) => (
        <circle key={`${particles}-${index}`} r={3.6} className={`particle ${lane !== undefined ? `lane-fill-${lane}` : `particle-${kind}`}`}>
          <animateMotion dur="1.8s" repeatCount="indefinite" begin={`${(-index * 1.8) / particles}s`} path={path} />
        </circle>
      ))}
      {kind !== "control" && edgeRate !== null && edgeRate > 0.5 && (
        <EdgeLabelRenderer>
          <div className="edge-label" style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
            {rate(edgeRate)}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

const nodeTypes = {
  doors: DoorsNode,
  lane: LaneNode,
  cashier: CashierNode,
  manager: ManagerNode,
  ledger: LedgerNode,
  desk: DeskNode,
};
const edgeTypes = { flow: FlowEdge };

export function FlowMap({
  floor,
  selected,
  onSelect,
  reducedMotion,
  cpuTarget,
}: {
  floor: FloorModel;
  selected: string | null;
  onSelect: (id: string | null) => void;
  reducedMotion: boolean;
  cpuTarget: number | null;
}) {
  const [nodeHeights, setNodeHeights] = useState<Record<string, number>>({});
  const cashierCount = Math.max(1, floor.cashiers.length);
  const rows = Math.max(floor.lanes.length, cashierCount);
  const expanded = rows > 6;
  // Assignments and fault notices can change a card's height during handover.
  // Stack measured cards instead of assuming each consumer occupies one row.
  const { laneY, cashierY, extent } = useMemo(() => {
    const laneY: number[] = [];
    const cashierY: number[] = [];
    let laneBottom = 140;
    let cashierBottom = 140;
    for (const lane of floor.lanes) {
      laneY.push(laneBottom);
      laneBottom += (nodeHeights[`lane${lane.partition}`] ?? 150) + 18;
    }
    for (const cashier of floor.cashiers) {
      cashierY.push(cashierBottom);
      const estimatedHeight = 200 + Math.max(0, Math.ceil(cashier.lanes.length / 3) - 1) * 24 + (cashier.faults.length ? 28 : 0);
      cashierBottom += (nodeHeights[`cashier:${cashier.name}`] ?? estimatedHeight) + 24;
    }
    return { laneY, cashierY, extent: Math.max(laneBottom, cashierBottom) };
  }, [floor.lanes, floor.cashiers, nodeHeights]);
  const middle = 140 + ((rows - 1) * ROW_SPACING) / 2;
  const built = useMemo<Node<Data>[]>(() => {
    const list: Node<Data>[] = [
      { id: "doors", type: "doors", position: { x: 0, y: rows > 6 ? 270 : middle }, data: { doors: floor.doors } },
      {
        id: "manager",
        type: "manager",
        position: { x: 660, y: 0 },
        data: { manager: floor.manager, partitionCount: floor.lanes.length },
      },
      { id: "ledger", type: "ledger", position: { x: 1030, y: rows > 6 ? 180 : middle - 100 }, data: { ledger: floor.ledger } },
      { id: "desk", type: "desk", position: { x: 1030, y: rows > 6 ? 430 : middle + 150 }, data: { desk: floor.desk } },
    ];
    floor.lanes.forEach((lane, index) =>
      list.push({
        id: `lane${lane.partition}`,
        type: "lane",
        position: { x: 300, y: expanded ? laneY[index] : 140 + index * ROW_SPACING },
        data: { lane, scale: floor.queueScale },
      }),
    );
    floor.cashiers.forEach((cashier, index) =>
      list.push({
        id: `cashier:${cashier.name}`,
        type: "cashier",
        position: { x: 660, y: expanded ? cashierY[index] : 140 + ((rows - cashierCount) * ROW_SPACING) / 2 + index * ROW_SPACING },
        data: { cashier, cpuTarget },
      }),
    );
    return list.map((node) => ({ ...node, selected: node.id === selected, draggable: false }));
  }, [floor, selected, cashierCount, cpuTarget, rows, middle, expanded, laneY, cashierY]);
  // Controlled nodes: React Flow's measured sizes must survive each second's rebuild,
  // otherwise a node can stay unmeasured and therefore hidden.
  const [nodes, setNodes] = useState<Node<Data>[]>(built);
  useEffect(() => {
    setNodes((current) => {
      const previous = new Map(current.map((node) => [node.id, node]));
      return built.map((node) => {
        const before = previous.get(node.id);
        return before?.measured ? { ...node, measured: before.measured } : node;
      });
    });
  }, [built]);
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<Data>>[]) => {
      setNodeHeights((current) => {
        let next = current;
        for (const change of changes) {
          if (change.type !== "dimensions" || !change.dimensions?.height) continue;
          if (current[change.id] === change.dimensions.height) continue;
          if (next === current) next = { ...current };
          next[change.id] = change.dimensions.height;
        }
        return next;
      });
      setNodes((current) =>
        applyNodeChanges(
          changes.filter((change) => change.type === "dimensions"),
          current,
        ),
      );
    },
    [],
  );
  const edges = useMemo<Edge<Data>[]>(
    () =>
      floor.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        targetHandle: edge.kind === "control" ? "control" : undefined,
        type: "flow",
        data: { rate: edge.rate, kind: edge.kind, lane: edge.lane, reducedMotion },
      })),
    [floor.edges, reducedMotion],
  );
  return (
    <div
      className={expanded ? "expanded-flow-scroll" : undefined}
      role={expanded ? "region" : undefined}
      aria-label={expanded ? "Scrollable pipeline map" : undefined}
      tabIndex={expanded ? 0 : undefined}
    >
      <div className="flow-map" data-testid="flow-map" style={expanded ? { height: 160 + extent * 0.85 } : undefined}>
        <ReactFlow
          nodes={nodes}
          onNodesChange={onNodesChange}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.04 }}
          zoomOnScroll={rows <= 6}
          preventScrolling={rows <= 6}
          minZoom={0.15}
          maxZoom={1.6}
          nodesConnectable={false}
          nodesDraggable={false}
          elementsSelectable
          onNodeClick={(_, node) => onSelect(node.id)}
          onPaneClick={() => onSelect(null)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
