export interface ContradictionBadgeProps {
  /** Number of unresolved contradictions. */
  count?: number;
  /** Explicit flag; takes effect when `count` is not provided. */
  hasContradictions?: boolean;
  /** Render nothing when there are no contradictions (default true). */
  hideWhenClear?: boolean;
}

/**
 * Distinct warning indicator for memories with unresolved contradictions.
 *
 * Requirement 19.4: WHEN a memory has unresolved contradictions, display a
 * distinct warning indicator. The badge resolves "has contradictions" from
 * `count` when present, otherwise from `hasContradictions`.
 */
export function ContradictionBadge({
  count,
  hasContradictions,
  hideWhenClear = true,
}: ContradictionBadgeProps) {
  const resolvedCount = typeof count === "number" ? Math.max(0, count) : undefined;
  const flagged =
    resolvedCount !== undefined ? resolvedCount > 0 : Boolean(hasContradictions);

  if (!flagged) {
    return hideWhenClear ? null : (
      <span className="mg-contradiction mg-contradiction--clear" hidden />
    );
  }

  const labelCount =
    resolvedCount !== undefined ? ` (${resolvedCount})` : "";
  const ariaLabel =
    resolvedCount !== undefined
      ? `${resolvedCount} unresolved contradiction${resolvedCount === 1 ? "" : "s"}`
      : "Unresolved contradictions";

  return (
    <span className="mg-contradiction" role="status" aria-label={ariaLabel}>
      <span className="mg-contradiction__icon" aria-hidden="true">
        ⚠
      </span>
      <span>Contradiction{labelCount}</span>
    </span>
  );
}

export default ContradictionBadge;
