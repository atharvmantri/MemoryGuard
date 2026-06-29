import { useCallback, useEffect, useState } from "react";
import type { MemoryGuardApi, MemoryResponse } from "../lib/api";
import { api as defaultApi } from "../lib/api";
import { formatScope, truncate } from "../lib/format";
import TrustMeter from "../components/TrustMeter";
import ContradictionBadge from "../components/ContradictionBadge";
import StatusBadge from "../components/StatusBadge";

export interface MemoriesListProps {
  /** API client (injectable for tests). Defaults to the local-engine client. */
  api?: MemoryGuardApi;
  /** Called when a row is selected, to open the detail view. */
  onSelect?: (memoryId: string) => void;
}

/**
 * Memories list (Requirement 15.1 / 15.2): a read-focused table of stored
 * memories showing truncated content, source_ref, scope, a trust meter, the
 * lifecycle status, and a contradiction badge. Rows open the detail view.
 */
export function MemoriesList({ api = defaultApi, onSelect }: MemoriesListProps) {
  const [memories, setMemories] = useState<MemoryResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .listMemories()
      .then((items) => {
        if (active) setMemories(items);
      })
      .catch((err: unknown) => {
        if (active)
          setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api]);

  useEffect(() => load(), [load]);

  return (
    <section className="mg-card" aria-labelledby="memories-title">
      <div className="mg-card__header">
        <h2 className="mg-card__title" id="memories-title">
          Memories
        </h2>
        <button
          type="button"
          className="mg-btn mg-btn--ghost"
          onClick={() => load()}
        >
          Refresh
        </button>
      </div>

      {loading && <p className="mg-muted">Loading memories…</p>}

      {error && !loading && (
        <p className="mg-error" role="alert">
          Could not load memories: {error}
        </p>
      )}

      {!loading && !error && memories.length === 0 && (
        <p className="mg-muted">No memories stored yet.</p>
      )}

      {!loading && !error && memories.length > 0 && (
        <div className="mg-table-wrap">
          <table className="mg-table">
            <thead>
              <tr>
                <th scope="col">Content</th>
                <th scope="col">Source</th>
                <th scope="col">Scope</th>
                <th scope="col">Trust</th>
                <th scope="col">Status</th>
                <th scope="col">Contradictions</th>
              </tr>
            </thead>
            <tbody>
              {memories.map((m) => (
                <tr
                  key={m.memory_id}
                  className="mg-table__row"
                  tabIndex={0}
                  role="button"
                  aria-label={`Open memory ${m.memory_id}`}
                  onClick={() => onSelect?.(m.memory_id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect?.(m.memory_id);
                    }
                  }}
                >
                  <td className="mg-table__content">{truncate(m.content, 90)}</td>
                  <td>
                    <code className="mg-code">{m.source_ref}</code>
                  </td>
                  <td>{formatScope(m.scope, m.scope_ref)}</td>
                  <td className="mg-table__trust">
                    <TrustMeter trustScore={m.trust_score} label={m.memory_id} />
                  </td>
                  <td>
                    <StatusBadge status={m.status} />
                  </td>
                  <td>
                    <ContradictionBadge count={m.contradicts.length} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default MemoriesList;
