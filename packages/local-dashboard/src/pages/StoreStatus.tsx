import { useCallback, useEffect, useState } from "react";
import type { HealthResponse, MemoryGuardApi } from "../lib/api";
import { api as defaultApi } from "../lib/api";

export interface StoreStatusProps {
  api?: MemoryGuardApi;
}

interface StoreStatusState {
  health: HealthResponse | null;
  memoryCount: number | null;
}

/**
 * Store status panel (Requirement 15.4): shows memory counts and the storage
 * mode (= local). Pulls liveness + active flags from /v1/health and derives
 * the memory count from /v1/memories length.
 */
export function StoreStatus({ api = defaultApi }: StoreStatusProps) {
  const [state, setState] = useState<StoreStatusState>({
    health: null,
    memoryCount: null,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([api.getHealth(), api.listMemories()])
      .then(([health, memories]) => {
        if (!active) return;
        setState({ health, memoryCount: memories.length });
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

  const flags = state.health?.flags ?? {};
  const flagEntries = Object.entries(flags);

  return (
    <section className="mg-card" aria-labelledby="store-status-title">
      <div className="mg-card__header">
        <h2 className="mg-card__title" id="store-status-title">
          Store status
        </h2>
        <button
          type="button"
          className="mg-btn mg-btn--ghost"
          onClick={() => load()}
        >
          Refresh
        </button>
      </div>

      {loading && <p className="mg-muted">Loading status…</p>}
      {error && !loading && (
        <p className="mg-error" role="alert">
          Could not load status: {error}
        </p>
      )}

      {!loading && !error && (
        <>
          <div className="mg-stats">
            <div className="mg-stat">
              <span className="mg-stat__value">
                {state.memoryCount ?? "—"}
              </span>
              <span className="mg-stat__label">Memories</span>
            </div>
            <div className="mg-stat">
              <span className="mg-stat__value">
                {state.health?.mode ?? "local"}
              </span>
              <span className="mg-stat__label">Mode</span>
            </div>
            <div className="mg-stat">
              <span className="mg-stat__value">
                {state.health?.status ?? "—"}
              </span>
              <span className="mg-stat__label">Health</span>
            </div>
            {state.health?.version && (
              <div className="mg-stat">
                <span className="mg-stat__value">{state.health.version}</span>
                <span className="mg-stat__label">Version</span>
              </div>
            )}
          </div>

          {flagEntries.length > 0 && (
            <div className="mg-panel" aria-labelledby="flags-title">
              <h3 className="mg-panel__title" id="flags-title">
                Feature flags
              </h3>
              <ul className="mg-flaglist">
                {flagEntries.map(([name, enabled]) => (
                  <li
                    className="mg-flag"
                    key={name}
                    data-enabled={enabled ? "true" : "false"}
                  >
                    <span className="mg-flag__dot" aria-hidden="true" />
                    <span className="mg-flag__name">{name}</span>
                    <span className="mg-flag__state">
                      {enabled ? "on" : "off"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export default StoreStatus;
