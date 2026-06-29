import { useCallback, useEffect, useState } from "react";
import type {
  ContradictionEntry,
  MemoryGuardApi,
  MemoryResponse,
} from "../lib/api";
import { api as defaultApi } from "../lib/api";
import { formatDateTime, formatScope } from "../lib/format";
import TrustMeter from "../components/TrustMeter";
import ContradictionBadge from "../components/ContradictionBadge";
import StatusBadge from "../components/StatusBadge";

export interface MemoryDetailProps {
  memoryId: string;
  api?: MemoryGuardApi;
  /** Navigate to another memory (contradiction / lineage link). */
  onSelect?: (memoryId: string) => void;
  /** Return to the list view. */
  onBack?: () => void;
}

/** Pull lineage pointers out of the extensible metadata bag. */
function lineageIds(
  metadata: Record<string, unknown> | undefined,
  key: "supersedes" | "superseded_by",
): string[] {
  const value = metadata?.[key];
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String);
  return [String(value)];
}

/**
 * Memory detail (Requirement 15.2): full provenance (source_type, source_ref,
 * scope/scope_ref, created/updated), a trust signal breakdown via the trust
 * meter + signal rows, contradiction links, and lineage (supersedes /
 * superseded_by from metadata when present).
 */
export function MemoryDetail({
  memoryId,
  api = defaultApi,
  onSelect,
  onBack,
}: MemoryDetailProps) {
  const [memory, setMemory] = useState<MemoryResponse | null>(null);
  const [contradictions, setContradictions] = useState<ContradictionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([api.getMemory(memoryId), api.getContradictions(memoryId)])
      .then(([mem, contras]) => {
        if (!active) return;
        setMemory(mem);
        setContradictions(contras);
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
  }, [api, memoryId]);

  useEffect(() => load(), [load]);

  const supersedes = lineageIds(memory?.metadata, "supersedes");
  const supersededBy = lineageIds(memory?.metadata, "superseded_by");

  return (
    <section className="mg-card" aria-labelledby="memory-detail-title">
      <div className="mg-card__header">
        <h2 className="mg-card__title" id="memory-detail-title">
          Memory detail
        </h2>
        {onBack && (
          <button type="button" className="mg-btn mg-btn--ghost" onClick={onBack}>
            ← Back to list
          </button>
        )}
      </div>

      {loading && <p className="mg-muted">Loading memory…</p>}
      {error && !loading && (
        <p className="mg-error" role="alert">
          Could not load memory {memoryId}: {error}
        </p>
      )}

      {memory && !loading && !error && (
        <div className="mg-detail">
          <p className="mg-detail__content">{memory.content}</p>

          <div className="mg-detail__grid">
            {/* Provenance --------------------------------------------------- */}
            <div className="mg-panel" aria-labelledby="provenance-title">
              <h3 className="mg-panel__title" id="provenance-title">
                Provenance
              </h3>
              <dl className="mg-kv">
                <dt>Memory ID</dt>
                <dd><code className="mg-code">{memory.memory_id}</code></dd>
                <dt>Source type</dt>
                <dd>{memory.source_type}</dd>
                <dt>Source ref</dt>
                <dd><code className="mg-code">{memory.source_ref}</code></dd>
                <dt>Scope</dt>
                <dd>{formatScope(memory.scope, memory.scope_ref)}</dd>
                <dt>Sensitivity</dt>
                <dd>{memory.sensitivity}</dd>
                <dt>Status</dt>
                <dd><StatusBadge status={memory.status} /></dd>
                <dt>Created</dt>
                <dd>{formatDateTime(memory.created_at)}</dd>
                <dt>Updated</dt>
                <dd>{formatDateTime(memory.updated_at)}</dd>
                <dt>Expires</dt>
                <dd>{formatDateTime(memory.expires_at)}</dd>
                <dt>Tags</dt>
                <dd>
                  {memory.tags.length > 0 ? (
                    <span className="mg-tags">
                      {memory.tags.map((t) => (
                        <span className="mg-tag" key={t}>
                          {t}
                        </span>
                      ))}
                    </span>
                  ) : (
                    "—"
                  )}
                </dd>
              </dl>
            </div>

            {/* Trust signal breakdown -------------------------------------- */}
            <div className="mg-panel" aria-labelledby="trust-title">
              <h3 className="mg-panel__title" id="trust-title">
                Trust
              </h3>
              <div className="mg-detail__trust">
                <TrustMeter
                  trustScore={memory.trust_score}
                  label="Overall trust"
                />
              </div>
              <dl className="mg-kv">
                <dt>Trust score</dt>
                <dd>{memory.trust_score.toFixed(2)}</dd>
                <dt>Confirmations</dt>
                <dd>
                  {typeof memory.metadata?.confirmations === "number"
                    ? String(memory.metadata.confirmations)
                    : "—"}
                </dd>
                <dt>Contradictions</dt>
                <dd>
                  <ContradictionBadge count={memory.contradicts.length} />
                  {memory.contradicts.length === 0 && (
                    <span className="mg-muted"> none</span>
                  )}
                </dd>
              </dl>
            </div>
          </div>

          {/* Contradiction links ------------------------------------------- */}
          <div className="mg-panel" aria-labelledby="contradiction-links-title">
            <h3 className="mg-panel__title" id="contradiction-links-title">
              Contradiction links
            </h3>
            {memory.contradicts.length === 0 && contradictions.length === 0 ? (
              <p className="mg-muted">No contradictions recorded.</p>
            ) : (
              <ul className="mg-linklist">
                {(contradictions.length > 0
                  ? contradictions.map((c) => ({
                      id: c.memory_id,
                      reason: c.reason ?? null,
                      sourceRef: c.source_ref ?? null,
                      status: c.status ?? null,
                    }))
                  : memory.contradicts.map((id) => ({
                      id,
                      reason: null,
                      sourceRef: null,
                      status: null,
                    }))
                ).map((entry) => (
                  <li key={entry.id}>
                    <button
                      type="button"
                      className="mg-link"
                      onClick={() => onSelect?.(entry.id)}
                    >
                      {entry.id}
                    </button>
                    {entry.sourceRef && (
                      <code className="mg-code"> {entry.sourceRef}</code>
                    )}
                    {entry.status && (
                      <span className="mg-muted"> [{entry.status}]</span>
                    )}
                    {entry.reason && (
                      <span className="mg-muted"> — {entry.reason}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Lineage ------------------------------------------------------- */}
          <div className="mg-panel" aria-labelledby="lineage-title">
            <h3 className="mg-panel__title" id="lineage-title">
              Lineage
            </h3>
            {supersedes.length === 0 && supersededBy.length === 0 ? (
              <p className="mg-muted">No lineage recorded.</p>
            ) : (
              <div className="mg-lineage">
                {supersedes.length > 0 && (
                  <div>
                    <span className="mg-lineage__label">Supersedes</span>
                    <ul className="mg-linklist">
                      {supersedes.map((id) => (
                        <li key={id}>
                          <button
                            type="button"
                            className="mg-link"
                            onClick={() => onSelect?.(id)}
                          >
                            {id}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {supersededBy.length > 0 && (
                  <div>
                    <span className="mg-lineage__label">Superseded by</span>
                    <ul className="mg-linklist">
                      {supersededBy.map((id) => (
                        <li key={id}>
                          <button
                            type="button"
                            className="mg-link"
                            onClick={() => onSelect?.(id)}
                          >
                            {id}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export default MemoryDetail;
