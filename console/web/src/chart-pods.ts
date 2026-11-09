import { MAX_REPLICAS } from "./capacity";
import type { PodPoint } from "./types";

type PodHistoryPoint = {
  pods: Pick<PodPoint, "name" | "phase" | "deleting">[];
};

/** Keep live consumers visible when a chart window contains pod replacements. */
export function cpuPodSeries(points: readonly PodHistoryPoint[]): string[] {
  const names = new Set<string>();
  for (const pod of points.at(-1)?.pods ?? []) {
    if (pod.phase === "Running" && !pod.deleting) names.add(pod.name);
    if (names.size === MAX_REPLICAS) return [...names];
  }
  // Ended runs and replay still show their most recently observed consumers.
  for (let index = points.length - 1; index >= 0; index--) {
    for (const pod of points[index].pods) {
      names.add(pod.name);
      if (names.size === MAX_REPLICAS) return [...names];
    }
  }
  return [...names];
}
