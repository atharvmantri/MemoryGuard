import { useState } from "react";
import type {
  MemoryGuardApi,
  QueryResponse,
  Scope,
} from "../lib/api";
import { api as defaultApi } from "../lib/api";
import { formatScope, truncate } from "../lib/format";
import TrustMeter from "../components/TrustMeter";
import StatusBadge from "../components/StatusBadge";

export interface QueryPlaygroundProps {
  api?: MemoryGuardApi;
  /** Open a result memory in the detail view. */
  onSelect?: (memoryId: string) => void;
}

const SCOPES: Scope[] = [
  "global",
  "org",
  "project",
  "repo",
  "user",
  "session",
];

/**
 * Query playground (Requirement 15.3): a text input plus scope / min_trust
 * controls that POST to /v1/query and render results with their audit reasons
 * and trust meters.
 */
export function QueryPlayground({
  api = defaultApi,
  onSelect,
}: QueryPlaygroundProps) {
  const [text, setText] = useState("");
  const [scope, setScope] = useState<Scope | "">("");
  const [scopeRef, setScopeRef] = useState("");
  const [minTrust, setMinTrust] = useState(0);
  const [limit, setLimit] = useState(10);

  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runQuery(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim()) {
      setError("Enter query text to run a search.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.runQuery({
        text: text.trim(),
        scope: scope || undefined,
        scope_ref: scopeRef.trim() || undefined,
        min_trust: minTrust,
        limit,
      });
      setResponse(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setResponse(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="mg-card" aria-labelledby="query-title">
      <h2 className="mg-card__title" id="query-title">
        Query playground
      </h2>

      <form className="mg-form" onSubmit={runQuery}>
        <label className="mg-field mg-field--wide">
          <span className="mg-field__label">Query text</span>
          <input
            className="mg-input"
            type="text"
            value={text}
            placeholder="what database does the billing service use?"
            onChange={(e) => setText(e.target.value)}
          />
        </label>

        <label className="mg-field">
          <span className="mg-field__label">Scope</span>
          <select
            className="mg-input"
            value={scope}
            onChange={(e) => setScope(e.target.value as Scope | "")}
          >
            <option value="">any</option>
            {SCOPES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label className="mg-field">
          <span className="mg-field__label">Scope ref</span>
          <input
            className="mg-input"
            type="text"
            value={scopeRef}
            placeholder="billing-svc"
            onChange={(e) => setScopeRef(e.target.value)}
          />
        </label>

        <label className="mg-field">
          <span className="mg-field__label">
            Min trust: {minTrust.toFixed(2)}
          </span>
          <input
            className="mg-range"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minTrust}
            onChange={(e) => setMinTrust(Number(e.target.value))}
          />
        </label>

        <label className="mg-field">
          <span className="mg-field__label">Limit</span>
          <input
            className="mg-input"
            type="number"
            min={1}
            max={50}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
          />
        </label>

        <div className="mg-field mg-field--actions">
          <button type="submit" className="mg-btn" disabled={loading}>
            {loading ? "Running…" : "Run query"}
          </button>
        </div>
      </form>

      {error && (
        <p className="mg-error" role="alert">
          {error}
        </p>
      )}

      {response && !error && (
        <div className="mg-results">
          <p className="mg-muted">
            {response.results.length} result
            {response.results.length === 1 ? "" : "s"} ·{" "}
            <code className="mg-code">query_id: {response.query_id}</code>
          </p>

          {response.results.length === 0 ? (
            <p className="mg-muted">
              No trusted memories matched this query.
            </p>
          ) : (
            <ul className="mg-result-list">
              {response.results.map((r) => (
                <li className="mg-result" key={r.memory.memory_id}>
                  <div className="mg-result__head">
                    <button
                      type="button"
                      className="mg-link mg-result__title"
                      onClick={() => onSelect?.(r.memory.memory_id)}
                    >
                      {truncate(r.memory.content, 100)}
                    </button>
                    <StatusBadge status={r.memory.status} />
                  </div>

                  <div className="mg-result__meta">
                    <code className="mg-code">{r.memory.source_ref}</code>
                    <span className="mg-muted">
                      {formatScope(r.memory.scope, r.memory.scope_ref)}
                    </span>
                  </div>

                  <div className="mg-result__scores">
                    <TrustMeter
                      trustScore={r.memory.trust_score}
                      label="Trust"
                    />
                    <span className="mg-muted">
                      relevance {r.relevance.toFixed(2)} · rank{" "}
                      {r.final_rank.toFixed(2)}
                    </span>
                  </div>

                  {r.reasons.length > 0 && (
                    <ul className="mg-reasons" aria-label="Why this was surfaced">
                      {r.reasons.map((reason, i) => (
                        <li className="mg-reason" key={`${reason}-${i}`}>
                          {reason}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default QueryPlayground;
