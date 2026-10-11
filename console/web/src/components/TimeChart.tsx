import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { clock } from "../format";

export type SeriesSpec = {
  label: string;
  color: string; // CSS custom property name, e.g. "--series-b1"
  value: (index: number) => number | null;
  stepped?: boolean;
};

export type Marker = { t: number; label: string; tone: "action" | "fault" | "observation" };

function cssColor(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function useTheme() {
  const [dark, setDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  useEffect(() => {
    const query = matchMedia("(prefers-color-scheme: dark)");
    const update = () => setDark(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return dark;
}

const SYNC = uPlot.sync("streamscale");

/**
 * One measure per chart (never two y-scales), 2px lines, recessive grid, a synced
 * crosshair whose legend reads every series at that time, and gaps for unknowns.
 */
export function TimeChart({
  title,
  subtitle,
  times,
  series,
  range,
  markers,
  format,
  reference,
  testId,
  integer = false,
  yMax,
}: {
  title: string;
  subtitle?: string;
  times: number[];
  series: SeriesSpec[];
  range: [number, number] | null;
  markers: Marker[];
  format: (value: number | null) => string;
  reference?: { value: number; label: string } | null;
  testId?: string;
  integer?: boolean;
  yMax?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const markerRef = useRef(markers);
  const referenceRef = useRef(reference);
  markerRef.current = markers;
  referenceRef.current = reference;
  const dark = useTheme();
  const [table, setTable] = useState(false);
  const signature = series.map((item) => `${item.label}:${item.color}:${item.stepped}`).join("|");

  const data = useMemo<uPlot.AlignedData>(() => {
    const columns: (number | null)[][] = series.map((item) => times.map((_, index) => item.value(index)));
    return [times, ...columns] as uPlot.AlignedData;
  }, [times, series]);

  useEffect(() => {
    if (!container.current) return;
    const ink = cssColor("--muted");
    const grid = cssColor("--grid");
    const options: uPlot.Options = {
      width: container.current.clientWidth || 400,
      height: 150,
      padding: [8, 10, 0, 2],
      cursor: { sync: { key: SYNC.key }, drag: { x: false, y: false }, points: { size: 8, width: 2 } },
      legend: { show: true, live: true },
      scales: {
        x: { time: true },
        y: {
          range: (_u, min, max) => [
            Math.min(0, min ?? 0),
            yMax ?? Math.max(integer ? 3 : 1, (max ?? 1) * 1.12),
          ],
        },
      },
      axes: [
        {
          stroke: ink,
          grid: { stroke: grid, width: 1 },
          ticks: { stroke: grid, width: 1 },
          values: (_u, splits) => splits.map((value) => clock(value)),
          space: 46,
          font: "11px system-ui",
        },
        {
          stroke: ink,
          grid: { stroke: grid, width: 1 },
          ticks: { show: false },
          values: (_u, splits) => splits.map((value) => (integer && !Number.isInteger(value) ? "" : format(value))),
          size: 56,
          font: "11px system-ui",
        },
      ],
      series: [
        { value: (_u, value) => (value ? clock(value) : "—") },
        ...series.map((item) => ({
          label: item.label,
          stroke: cssColor(item.color),
          width: 2,
          points: { show: false },
          spanGaps: false,
          value: (_u: uPlot, value: number | null) => format(value),
          paths: item.stepped ? uPlot.paths.stepped!({ align: 1 }) : undefined,
        })),
      ],
      hooks: {
        draw: [
          (u) => {
            const context = u.ctx;
            const { top, height } = u.bbox;
            context.save();
            const value = referenceRef.current;
            if (value) {
              const y = u.valToPos(value.value, "y", true);
              if (y >= top && y <= top + height) {
                context.strokeStyle = cssColor("--ink-2");
                context.setLineDash([4, 4]);
                context.lineWidth = 1;
                context.beginPath();
                context.moveTo(u.bbox.left, y);
                context.lineTo(u.bbox.left + u.bbox.width, y);
                context.stroke();
                context.setLineDash([]);
                context.fillStyle = cssColor("--ink-2");
                context.font = `${11 * devicePixelRatio}px system-ui`;
                context.fillText(value.label, u.bbox.left + 6, y - 4 * devicePixelRatio);
              }
            }
            for (const marker of markerRef.current) {
              const x = u.valToPos(marker.t, "x", true);
              if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) continue;
              context.strokeStyle = cssColor(marker.tone === "fault" ? "--crit" : "--ink-2");
              context.globalAlpha = 0.55;
              context.lineWidth = 1;
              context.beginPath();
              context.moveTo(x, top);
              context.lineTo(x, top + height);
              context.stroke();
            }
            context.restore();
          },
        ],
      },
    };
    plot.current = new uPlot(options, data, container.current);
    SYNC.sub(plot.current);
    const observer = new ResizeObserver(() => {
      if (container.current && plot.current) {
        plot.current.setSize({ width: container.current.clientWidth, height: 150 });
      }
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      if (plot.current) SYNC.unsub(plot.current);
      plot.current?.destroy();
      plot.current = null;
    };
    // Rebuild only when the series definition or theme changes; data updates below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, dark]);

  useEffect(() => {
    const chart = plot.current;
    if (!chart) return;
    chart.setData(data, false);
    if (range) chart.setScale("x", { min: range[0], max: range[1] });
    else if (times.length) chart.setScale("x", { min: times[0], max: times[times.length - 1] });
    // Without a hovered time, the readout shows the latest sample rather than blanks.
    if (times.length && (chart.cursor.left ?? -1) < 0) chart.setLegend({ idx: times.length - 1 });
  }, [data, range, times]);

  useEffect(() => {
    plot.current?.redraw(false);
  }, [markers, reference]);

  const latest = times.length - 1;
  return (
    <figure className="chart" data-testid={testId}>
      <figcaption>
        <div>
          <strong>{title}</strong>
          {subtitle && <span className="muted"> · {subtitle}</span>}
        </div>
        <button className="link-button" onClick={() => setTable((value) => !value)} aria-pressed={table}>
          {table ? "Chart" : "Table"}
        </button>
      </figcaption>
      <div ref={container} className="chart-canvas" hidden={table} aria-label={`${title} chart`} role="img" />
      {table && (
        <div className="chart-table">
          <table className="mini">
            <thead>
              <tr>
                <th>Time</th>
                {series.map((item) => (
                  <th key={item.label}>{item.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {times
                .map((t, index) => ({ t, index }))
                .slice(-20)
                .reverse()
                .map(({ t, index }) => (
                  <tr key={t}>
                    <td>{clock(t)}</td>
                    {series.map((item) => (
                      <td key={item.label}>{format(item.value(index))}</td>
                    ))}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      {latest >= 0 && !table && series.length === 1 && (
        <p className="chart-now">Now: {format(series[0].value(latest))}</p>
      )}
    </figure>
  );
}
