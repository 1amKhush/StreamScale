import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const RECORD = path.resolve(here, "../../../artifacts/console/server.json");

export function server(): { url: string; login_url: string } {
  if (!fs.existsSync(RECORD)) {
    throw new Error("Start the console first: ./streamscale.sh console (or make console-serve)");
  }
  return JSON.parse(fs.readFileSync(RECORD, "utf8"));
}

export async function login(page: Page) {
  await page.goto(server().login_url);
}

export const evidence = path.resolve(here, "../../../artifacts/console/e2e");

/** POST an operation as the logged-in browser context would (same cookies and Origin). */
export async function operation(page: Page, kind: string, params: Record<string, unknown> = {}) {
  const { url } = server();
  return page.request.post(`${url}/api/operations`, {
    headers: { Origin: url, "Content-Type": "application/json" },
    data: { kind, params, key: `e2e-${kind}-${Date.now()}-${Math.random().toString(36).slice(2)}` },
  });
}

/** Successful sale starts recorded since a moment, independent of the journal window. */
export async function startsSince(page: Page, since: number) {
  const state = await (await page.request.get(`${server().url}/api/state`)).json();
  return state.journal.filter(
    (entry: { kind?: string; state?: string; at: number }) =>
      entry.kind === "sale.start" && entry.state === "succeeded" && entry.at >= since,
  ).length;
}

/** Leave the floor as the next test expects: no sale publishing. */
export async function stopActiveSale(page: Page) {
  const state = await (await page.request.get(`${server().url}/api/state`)).json();
  if (["preparing", "publishing", "paused"].includes(state.sample?.sale?.state)) await operation(page, "sale.stop");
}
