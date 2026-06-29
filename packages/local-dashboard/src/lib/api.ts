/**
 * Typed REST client for the MemoryGuard OSS core API (apps/api).
 *
 * Mirrors the Pydantic schemas in design.md ("Request/Response Schemas") and
 * the OSS core routes (`/v1/memories`, `/v1/memories/{id}`,
 * `/v1/memories/{id}/contradictions`, `/v1/query`, `/v1/health`,
 * `/v1/projects`). Used by the Vault Mesh dashboard pages (tasks 19.2 / 19.3).
 *
 * Requirements 15.1 / 15.2: the local dashboard reads memories + provenance and
 * runs trust-aware queries against the local engine over this client.
 *
 * Local-first: the default base URL targets the loopback API the OSS engine
 * serves on (127.0.0.1:8000). No external hosts are contacted.
 */

// --- Core enums (string unions mirroring core MemoryRecord enums) ----------

export type SourceType =
  | "user"
  | "file"
  | "commit"
  | "slack"
  | "jira"
  | "api";

export type Scope =
  | "global"
  | "org"
  | "project"
  | "repo"
  | "user"
  | "session";

export type Sensitivity = "public" | "internal" | "secret" | "pii";

export type MemoryStatus =
  | "active"
  | "corrected"
  | "superseded"
  | "outdated"
  | "expired"
  | "deleted"
  | "disputed";

// --- Response schemas ------------------------------------------------------

/** A single memory with provenance + trust (GET /v1/memories/{id}). */
export interface MemoryResponse {
  memory_id: string;
  content: string;
  source_type: SourceType;
  source_ref: string;
  scope: Scope;
  scope_ref: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  trust_score: number;
  sensitivity: Sensitivity;
  status: MemoryStatus;
  contradicts: string[];
  tags: string[];
  /** Extensible bag (connectors/enterprise + lineage pointers). */
  metadata?: Record<string, unknown>;
}

/** One retrieved memory from POST /v1/query, with audit reasons. */
export interface RetrievedMemoryResponse {
  memory: MemoryResponse;
  relevance: number;
  final_rank: number;
  reasons: string[];
}

/** POST /v1/query response — results + audit correlation id. */
export interface QueryResponse {
  results: RetrievedMemoryResponse[];
  query_id: string;
}

/** POST /v1/query request body. */
export interface QueryRequest {
  text: string;
  scope?: Scope | null;
  scope_ref?: string | null;
  min_trust?: number;
  max_sensitivity?: Sensitivity;
  limit?: number;
}

/**
 * One contradiction edge for GET /v1/memories/{id}/contradictions.
 *
 * Mirrors the OSS API `ContradictionResponse` (apps/api schemas.py): each entry
 * describes the *contradicting* memory, so `memory_id` is the link target for
 * the contradiction. `source_ref`, `status`, and `confidence` are the
 * contradicting memory's provenance/lifecycle and the detector's confidence.
 */
export interface ContradictionEntry {
  /** Identifier of the contradicting memory (navigation/link target). */
  memory_id: string;
  /** Provenance of the contradicting memory, when available. */
  source_ref?: string | null;
  /** Lifecycle status of the contradicting memory, when available. */
  status?: MemoryStatus | string | null;
  /** Human-readable reason the two memories conflict. */
  reason?: string | null;
  /** Detector confidence in [0, 1], when available. */
  confidence?: number | null;
}

/** GET /v1/health response — liveness + active feature flags + mode. */
export interface HealthResponse {
  status: string;
  mode: string;
  version?: string;
  flags: Record<string, boolean>;
}

/** GET /v1/projects response entry (local project stores). */
export interface ProjectResponse {
  scope_ref: string;
  name?: string;
  memory_count?: number;
}

export interface ContextFileStatus {
  path: string;
  exists: boolean;
  managed: boolean;
  size: number;
}

export interface ContextSyncResponse {
  files: ContextFileStatus[];
  last_sync_time: string | null;
  pending_diff: string;
  pending_files: string[];
}

/** Filters for GET /v1/memories. */
export interface ListMemoriesParams {
  scope?: Scope;
  scope_ref?: string;
  status?: MemoryStatus;
}

// --- Errors ----------------------------------------------------------------

/** Raised when the API responds with a non-2xx status. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

// --- Client ----------------------------------------------------------------

export const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

export interface ApiClientConfig {
  /** Base URL for the API (no trailing slash needed). */
  baseUrl?: string;
  /** Injectable fetch (defaults to global fetch); handy for tests. */
  fetch?: FetchLike;
}

function resolveBaseUrl(explicit?: string): string {
  if (explicit) return explicit.replace(/\/+$/, "");
  // Allow a Vite env override without requiring it to be set.
  const env =
    typeof import.meta !== "undefined"
      ? (import.meta as { env?: Record<string, string | undefined> }).env
      : undefined;
  const fromEnv = env?.VITE_MG_API_BASE_URL;
  return (fromEnv ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
}

function buildQueryString(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, value);
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export interface MemoryGuardApi {
  readonly baseUrl: string;
  listMemories(params?: ListMemoriesParams): Promise<MemoryResponse[]>;
  getMemory(memoryId: string): Promise<MemoryResponse>;
  getContradictions(memoryId: string): Promise<ContradictionEntry[]>;
  runQuery(request: QueryRequest): Promise<QueryResponse>;
  getHealth(): Promise<HealthResponse>;
  getProjects(): Promise<ProjectResponse[]>;
  getContextSync(): Promise<ContextSyncResponse>;
  approveContextSync(): Promise<ContextSyncResponse>;
  rejectContextSync(): Promise<ContextSyncResponse>;
}

/**
 * Create a configured API client. Functions return typed, parsed results and
 * throw {@link ApiError} on non-2xx responses.
 */
export function createApiClient(config: ApiClientConfig = {}): MemoryGuardApi {
  const baseUrl = resolveBaseUrl(config.baseUrl);
  const doFetch: FetchLike =
    config.fetch ?? ((input, init) => fetch(input, init));

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await doFetch(`${baseUrl}${path}`, {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...(init?.headers ?? {}),
        },
      });
    } catch (cause) {
      throw new ApiError(
        0,
        `Network error contacting ${baseUrl}${path}: ${
          cause instanceof Error ? cause.message : String(cause)
        }`,
      );
    }

    let body: unknown = undefined;
    const text = await response.text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }

    if (!response.ok) {
      const message =
        (body && typeof body === "object" && "error" in body
          ? String((body as Record<string, unknown>).error)
          : undefined) ?? `Request to ${path} failed (${response.status})`;
      throw new ApiError(response.status, message, body);
    }

    return body as T;
  }

  return {
    baseUrl,

    async listMemories(params = {}) {
      const qs = buildQueryString({
        scope: params.scope,
        scope_ref: params.scope_ref,
        status: params.status,
      });
      const data = await request<MemoryResponse[] | { memories: MemoryResponse[] }>(
        `/v1/memories${qs}`,
      );
      return Array.isArray(data) ? data : data.memories ?? [];
    },

    async getMemory(memoryId) {
      return request<MemoryResponse>(
        `/v1/memories/${encodeURIComponent(memoryId)}`,
      );
    },

    async getContradictions(memoryId) {
      const data = await request<
        ContradictionEntry[] | { contradictions: ContradictionEntry[] }
      >(`/v1/memories/${encodeURIComponent(memoryId)}/contradictions`);
      return Array.isArray(data) ? data : data.contradictions ?? [];
    },

    async runQuery(req) {
      return request<QueryResponse>(`/v1/query`, {
        method: "POST",
        body: JSON.stringify(req),
      });
    },

    async getHealth() {
      return request<HealthResponse>(`/v1/health`);
    },

    async getProjects() {
      const data = await request<
        ProjectResponse[] | { projects: ProjectResponse[] }
      >(`/v1/projects`);
      return Array.isArray(data) ? data : data.projects ?? [];
    },

    async getContextSync() {
      return request<ContextSyncResponse>(`/v1/context`);
    },

    async approveContextSync() {
      return request<ContextSyncResponse>(`/v1/context/approve`, {
        method: "POST",
      });
    },

    async rejectContextSync() {
      return request<ContextSyncResponse>(`/v1/context/reject`, {
        method: "POST",
      });
    },
  };
}

/** Default client targeting the local engine on 127.0.0.1:8000. */
export const api = createApiClient();
