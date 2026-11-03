import { expect, test } from "@playwright/test";
import { login, server } from "./session";

const screenshots = "../../artifacts/ui-refinement";

test("Docs gives a quiet first-run guide, searchable technical reference and copyable commands", async ({ page, context }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await login(page);
  await page.getByTestId("tab-guide").click();
  await expect(page.getByRole("heading", { name: "Docs & guides" })).toBeVisible();
  await page.getByRole("link", { name: "Skip to main content" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  await expect(page).toHaveURL(/#docs$/);
  await expect(page.getByRole("heading", { name: "Your first sale, in five steps" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Live pipeline summary" })).toHaveCount(0);
  await expect(page.getByTestId("sale-bar")).toHaveCount(0);
  await page.getByRole("button", { name: "Copy command: First setup and console", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Copied. Paste" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("./streamscale.sh cluster\n./streamscale.sh console");
  await page.getByLabel("What would you like to know?").fill("Bayesian");
  await page.getByTestId("docs-topic-scaling").click();
  await expect(page.getByTestId("docs")).toContainText("Gaussian-process Bayesian optimization");
  await page.screenshot({ path: `${screenshots}/docs-scaling.png`, fullPage: true });
  await page.getByLabel("What would you like to know?").fill("no-such-technical-topic-xyz");
  await expect(page.getByRole("heading", { name: "No matching topics" })).toBeVisible();
  await page.getByRole("button", { name: "Browse all topics" }).click();
  await page.getByTestId("docs-topic-guided").click();
  await page.getByRole("radio", { name: /Crash and replay/ }).click();
  await expect(page.getByTestId("guide")).toContainText("faint after the next ledger entry");
  await page.getByTestId("docs-topic-commands").click();
  await page.getByTestId("docs-topic-guided").click();
  await expect(page.getByRole("radio", { name: /Crash and replay/ })).toHaveAttribute("aria-checked", "true");
  expect(errors).toEqual([]);
});

test("Docs links survive refresh and browser history", async ({ page }) => {
  await login(page);
  await page.goto(`${server().url}/#docs/architecture`);
  await expect(page.getByTestId("docs-topic-architecture")).toHaveAttribute("aria-current", "page");
  await page.reload();
  await expect(page.getByTestId("docs-topic-architecture")).toHaveAttribute("aria-current", "page");
  await page.getByTestId("docs-topic-commands").click();
  await expect(page).toHaveURL(/#docs\/commands$/);
  await page.goBack();
  await expect(page.getByTestId("docs-topic-architecture")).toHaveAttribute("aria-current", "page");
  await page.screenshot({ path: `${screenshots}/docs-architecture.png`, fullPage: true });
});

test("all documentation stays readable on a phone in dark mode", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await login(page);
  await page.getByTestId("tab-guide").click();
  for (const topic of ["start", "workflows", "scaling", "architecture", "commands", "troubleshooting", "guided"]) {
    await page.getByTestId(`docs-topic-${topic}`).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByTestId("docs-topic-commands").click();
  await page.screenshot({ path: `${screenshots}/docs-mobile-dark.png`, fullPage: true });
});
