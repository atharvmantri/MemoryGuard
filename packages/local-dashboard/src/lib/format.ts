/**
 * Small presentational helpers for the Vault Mesh dashboard.
 * Kept pure + dependency-free so they're easy to unit test.
 */

/** Truncate text to `max` characters, appending an ellipsis when clipped. */
export function truncate(text: string, max = 120): string {
  if (max <= 0) return "";
  if (text.length <= max) return text;
  return `${text.slice(0, max).trimEnd()}…`;
}

/**
 * Format an ISO-8601 timestamp for display. Returns the original string when
 * it cannot be parsed (so we never hide raw provenance behind "Invalid Date").
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Human label for a scope + scope_ref pair, e.g. "repo: billing-svc". */
export function formatScope(
  scope: string,
  scopeRef?: string | null,
): string {
  return scopeRef ? `${scope}: ${scopeRef}` : scope;
}
