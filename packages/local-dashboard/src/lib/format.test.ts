import { describe, it, expect } from "vitest";
import { truncate, formatDateTime, formatScope } from "./format";

describe("truncate", () => {
  it("returns the original string when within the limit", () => {
    expect(truncate("hello", 10)).toBe("hello");
  });

  it("clips and appends an ellipsis when over the limit", () => {
    expect(truncate("abcdefghij", 5)).toBe("abcde…");
  });

  it("returns empty string for a non-positive max", () => {
    expect(truncate("anything", 0)).toBe("");
  });
});

describe("formatDateTime", () => {
  it("returns an em dash for empty values", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
  });

  it("passes through unparseable strings rather than showing Invalid Date", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });

  it("formats a valid ISO timestamp to a non-empty string", () => {
    const out = formatDateTime("2024-01-01T00:00:00Z");
    expect(out).not.toBe("—");
    expect(out.length).toBeGreaterThan(0);
  });
});

describe("formatScope", () => {
  it("joins scope and scope_ref", () => {
    expect(formatScope("repo", "billing-svc")).toBe("repo: billing-svc");
  });

  it("returns just the scope when no scope_ref", () => {
    expect(formatScope("global")).toBe("global");
    expect(formatScope("global", null)).toBe("global");
  });
});
