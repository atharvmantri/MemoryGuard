import { useCallback, useEffect, useState } from "react";
import type { ContextSyncResponse, MemoryGuardApi } from "../lib/api";
import { api as defaultApi } from "../lib/api";

export interface ContextSyncProps {
  api?: MemoryGuardApi;
}

export function ContextSync({ api = defaultApi }: ContextSyncProps) {
  const [state, setState] = useState<ContextSyncResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .getContextSync()
      .then((next) => {
        if (active) setState(next);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api]);

  useEffect(() => load(), [load]);

  const act = (fn: () => Promise<ContextSyncResponse>) => {
    setBusy(true);
    setError(null);
    fn()
      .then(setState)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setBusy(false));
  };

  const hasPending = Boolean(state?.pending_files.length);

  return (
    <section className="mg-card" aria-labelledby="context-sync-title">
      <div className="mg-card__header">
        <h2 className="mg-card__title" id="context-sync-title">
          Context Sync
        </h2>
        <div className="mg-actions">
          {hasPending && (
            <>
              <button
                type="button"
                className="mg-btn"
                disabled={busy}
                onClick={() => act(() => api.approveContextSync())}
              >
                Approve
              </button>
              <button
                type="button"
                className="mg-btn mg-btn--ghost"
                disabled={busy}
                onClick={() => act(() => api.rejectContextSync())}
              >
                Reject
              </button>
            </>
          )}
          <button
            type="button"
            className="mg-btn mg-btn--ghost"
            disabled={busy}
            onClick={() => load()}
          >
            Refresh
          </button>
        </div>
      </div>

      {loading && <p className="mg-muted">Loading context sync...</p>}
      {error && !loading && (
        <p className="mg-error" role="alert">
          Could not load context sync: {error}
        </p>
      )}

      {!loading && !error && state && (
        <>
          <div className="mg-stats">
            <div className="mg-stat">
              <span className="mg-stat__value">{state.files.length}</span>
              <span className="mg-stat__label">Files</span>
            </div>
            <div className="mg-stat">
              <span className="mg-stat__value">
                {state.pending_files.length}
              </span>
              <span className="mg-stat__label">Pending</span>
            </div>
            <div className="mg-stat">
              <span className="mg-stat__value">
                {state.last_sync_time ? "synced" : "never"}
              </span>
              <span className="mg-stat__label">Last sync</span>
            </div>
          </div>

          <div className="mg-table-wrap">
            <table className="mg-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Status</th>
                  <th>Managed</th>
                  <th>Size</th>
                </tr>
              </thead>
              <tbody>
                {state.files.map((file) => (
                  <tr key={file.path}>
                    <td className="mg-code">{file.path}</td>
                    <td>{file.exists ? "present" : "missing"}</td>
                    <td>{file.managed ? "yes" : "no"}</td>
                    <td>{file.size}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mg-panel" aria-labelledby="context-diff-title">
            <h3 className="mg-panel__title" id="context-diff-title">
              Pending diff
            </h3>
            {state.pending_diff ? (
              <pre className="mg-diff">{state.pending_diff}</pre>
            ) : (
              <p className="mg-muted">No pending diff.</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export default ContextSync;
