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
