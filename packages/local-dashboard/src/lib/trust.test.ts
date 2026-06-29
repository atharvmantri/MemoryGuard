import { describe, it, expect } from "vitest";
import {
  clampTrustScore,
  trustLevel,
  trustPercentLabel,
  TRUST_HIGH_THRESHOLD,
  TRUST_MEDIUM_THRESHOLD,
} from "./trust";

describe("clampTrustScore", () => {
  it("clamps below 0 to 0 and above 1 to 1", () => {
    expect(clampTrustScore(-0.5)).toBe(0);
    expect(clampTrustScore(1.5)).toBe(1);
  });

  it("passes through in-range values", () => {
    expect(clampTrustScore(0.42)).toBe(0.42);
  });

  it("treats NaN as 0", () => {
    expect(clampTrustScore(Number.NaN)).toBe(0);
  });
});

describe("trustLevel (Requirement 19.3)", () => {
  it("is high at and above 0.7", () => {
    expect(trustLevel(TRUST_HIGH_THRESHOLD)).toBe("high");
    expect(trustLevel(0.7)).toBe("high");
    expect(trustLevel(0.95)).toBe("high");
    expect(trustLevel(1)).toBe("high");
  });

  it("is medium between 0.4 (inclusive) and 0.7 (exclusive)", () => {
    expect(trustLevel(TRUST_MEDIUM_THRESHOLD)).toBe("medium");
    expect(trustLevel(0.4)).toBe("medium");
    expect(trustLevel(0.55)).toBe("medium");
    expect(trustLevel(0.699)).toBe("medium");
  });

  it("is low below 0.4", () => {
    expect(trustLevel(0.399)).toBe("low");
    expect(trustLevel(0.1)).toBe("low");
    expect(trustLevel(0)).toBe("low");
  });

  it("clamps out-of-range scores before classifying", () => {
    expect(trustLevel(2)).toBe("high");
    expect(trustLevel(-1)).toBe("low");
  });
});

describe("trustPercentLabel", () => {
  it("formats as a rounded percentage", () => {
    expect(trustPercentLabel(0.732)).toBe("73%");
    expect(trustPercentLabel(0)).toBe("0%");
    expect(trustPercentLabel(1)).toBe("100%");
  });
});
