export const UNKNOWN = "—";

export function count(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return Math.round(value).toLocaleString("en-US");
}

export function rate(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  if (Math.abs(value) >= 100) return `${Math.round(value).toLocaleString("en-US")}/s`;
  return `${value.toFixed(1)}/s`;
}

export function seconds(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(value < 10 ? 2 : 1)} s`;
  const minutes = Math.floor(value / 60);
  return `${minutes} min ${Math.round(value % 60)} s`;
}

export function age(value: number | null | undefined) {
  if (value === null || value === undefined) return UNKNOWN;
  if (value < 1.5) return "now";
  if (value < 90) return `${Math.round(value)} s ago`;
  if (value < 5400) return `${Math.round(value / 60)} min ago`;
  return `${(value / 3600).toFixed(1)} h ago`;
}

export function percent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return UNKNOWN;
  return `${Math.round(value)}%`;
}

export function bytes(value: number | null | undefined) {
  if (value === null || value === undefined) return UNKNOWN;
  if (value >= 2 ** 30) return `${(value / 2 ** 30).toFixed(2)} GiB`;
  return `${Math.round(value / 2 ** 20)} MiB`;
}

export function clock(epochSeconds: number | null | undefined) {
  if (!epochSeconds) return UNKNOWN;
  return new Date(epochSeconds * 1000).toLocaleTimeString("en-GB", { hour12: false });
}

export function shortPod(name: string) {
  const parts = name.split("-");
  return parts.length > 2 ? parts.slice(-1)[0] : name;
}
