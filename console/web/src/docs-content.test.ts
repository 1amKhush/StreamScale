import { describe, expect, it } from "vitest";
import { DOC_ARTICLES, searchDocs } from "./docs-content";

describe("documentation discovery", () => {
  it("offers the complete learning path when search is empty", () => {
    expect(searchDocs("  ").map((article) => article.id)).toEqual([
      "start", "workflows", "scaling", "architecture", "commands", "troubleshooting", "guided",
    ]);
  });

  it("finds technical concepts, commands and guided demos without case sensitivity", () => {
    expect(searchDocs("BAYESIAN").map((article) => article.id)).toContain("scaling");
    expect(searchDocs("stop-cluster").map((article) => article.id)).toContain("commands");
    expect(searchDocs("crash replay").map((article) => article.id)).toContain("guided");
    expect(searchDocs("missing calibration").map((article) => article.id)).toContain("troubleshooting");
  });

  it("requires all search words, and has an explicit empty-result state", () => {
    expect(searchDocs("bayesian keyboard")).toEqual([]);
    expect(searchDocs("this-topic-does-not-exist")).toEqual([]);
  });

  it("keeps executable examples within the maintained launcher", () => {
    const commands = DOC_ARTICLES.flatMap((article) => article.sections.flatMap((section) => section.command?.text.split("\n") ?? []));
    expect(commands.length).toBeGreaterThan(10);
    for (const command of commands) expect(command).toMatch(/^\.\/streamscale\.sh [a-z-]+(?: --[a-z-]+(?: \d+)*)*$/);
  });
});
