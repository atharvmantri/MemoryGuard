/**
 * Trust-score helpers for the Vault Mesh dashboard.
 *
 * Trust levels follow Requirement 19.3 / design.md:
 *   - high   : score >= 0.7  (Circuit Blue / Signal Lime)
 *   - medium : 0.4 <= score < 0.7 (amber)
 *   - low    : score < 0.4  (red)
 */
export type TrustLevel = "high" | "medium" | "low";

export const TRUST_HIGH_THRESHOLD = 0.7;
export const TRUST_MEDIUM_THRESHOLD = 0.4;

/** Clamp an arbitrary number into the valid trust range [0, 1]. */
export function clampTrustScore(score: number): number {
  if (Number.isNaN(score)) return 0;
  if (score < 0) return 0;
  if (score > 1) return 1;
  return score;
}

/** Map a trust score (0..1) to its qualitative level. */
export function trustLevel(score: number): TrustLevel {
  const s = clampTrustScore(score);
  if (s >= TRUST_HIGH_THRESHOLD) return "high";
  if (s >= TRUST_MEDIUM_THRESHOLD) return "medium";
  return "low";
}

/** Human-readable percentage label, e.g. 0.732 -> "73%". */
export function trustPercentLabel(score: number): string {
  return `${Math.round(clampTrustScore(score) * 100)}%`;
}
