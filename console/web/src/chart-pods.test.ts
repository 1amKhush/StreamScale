import { describe, expect, it } from "vitest";
import { cpuPodSeries } from "./chart-pods";
import type { PodPoint } from "./types";

function pod(name: string, overrides: Partial<Pick<PodPoint, "phase" | "deleting">> = {}) {
  return { name, phase: "Running", deleting: false, ...overrides };
}

describe("CPU chart pod selection", () => {
  it("keeps a long-lived current pod when more than twelve replacements appear in the window", () => {
    const survivor = pod("long-lived");
    const history = Array.from({ length: 13 }, (_, index) => ({ pods: [survivor, pod(`retired-${index}`)] }));
    history.push({ pods: [survivor, pod("new-active")] });

    const selected = cpuPodSeries(history);
    expect(selected).toHaveLength(12);
    expect(selected.slice(0, 2)).toEqual(["long-lived", "new-active"]);
    expect(selected.slice(2)).toEqual(Array.from({ length: 10 }, (_, index) => `retired-${12 - index}`));
  });

  it("reserves all twelve slots for current running consumers over terminating or pending pods", () => {
    const active = Array.from({ length: 12 }, (_, index) => pod(`active-${index}`));
    const selected = cpuPodSeries([
      { pods: [pod("old")] },
      { pods: [pod("terminating", { deleting: true }), pod("pending", { phase: "Pending" }), ...active] },
    ]);
    expect(selected).toEqual(active.map((item) => item.name));
  });

  it("fills unused slots with recent historical pods for a drained replay and avoids duplicates", () => {
    expect(cpuPodSeries([
      { pods: [pod("oldest"), pod("survivor")] },
      { pods: [pod("survivor"), pod("latest-retired", { deleting: true })] },
      { pods: [] },
    ])).toEqual(["survivor", "latest-retired", "oldest"]);
    expect(cpuPodSeries([])).toEqual([]);
  });
});
