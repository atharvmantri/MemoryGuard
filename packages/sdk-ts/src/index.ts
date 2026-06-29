/**
 * @memoryguard/sdk — MemoryGuard OSS TypeScript SDK (REST client).
 *
 * This is the public surface of the TypeScript SDK: a remote client created
 * with `MemoryGuard.remote({ baseUrl, token })` that targets the MemoryGuard
 * REST API (`apps/api`, the OSS `/v1` routes). It exposes the same conceptual
 * operations as the Python SDK — `add`, `get`, `query`, `ingestPath`,
 * `correct`, `delete`, `contradictions` — over typed request/response models
 * that mirror the REST schemas in `apps/api/memoryguard_api/schemas.py`.
 *
 * Wire shapes vs. SDK shapes: the REST API speaks snake_case (`trust_score`,
 * `source_ref`), while this SDK presents idiomatic camelCase to TypeScript
 * callers (`trustScore`, `sourceRef`) per the design's TypeScript SDK example.
 * The mapping between the two is performed by the request serialization /
 * response deserialization layer.
 *
 * Scaffold status (task 24.1): this module defines the full type surface and
 * method signatures. Method bodies are `TODO` stubs that throw
 * `Error("TODO: not implemented")`; the real REST client implementation
 * (fetch, serialization, error mapping) lands in task 24.2.
 *
 * Requirements: 12.1 (remote constructor targeting the REST API base URL with
 * an optional auth token), 12.2 (exposes add/get/query/ingestPath/correct/
 * delete/contradictions), 18.2 (delivered scaffold-first behind a stable
 * interface, then genuinely implemented — never falsely complete).
 *
 * Apache-2.0 OSS package. Mirrors the OSS core + REST schemas only; it MUST
 * NOT depend on any commercial package.
 */

// ---------------------------------------------------------------------------
// Core enums (mirror the core MemoryRecord enums / REST vocabulary)
//
// Enum *values* are the lowercase wire strings sent to / received from the
// REST API; enum *members* use PascalCase per the design's TS SDK example
// (e.g. `SourceType.File`, `Scope.Repo`, `Sensitivity.Internal`).
// ---------------------------------------------------------------------------

/** Where a memory came from (provenance source kind). */
export enum SourceType {
  User = "user",
  File = "file",
  Commit = "commit",
  /** Commercial connector. */
  Slack = "slack",
  /** Commercial connector. */
  Jira = "jira",
  Api = "api",
}

/** Visibility boundary of a memory. */
export enum Scope {
  Global = "global",
  Org = "org",
  Project = "project",
  Repo = "repo",
  User = "user",
  Session = "session",
}

/** Data-sensitivity tier of a memory. */
export enum Sensitivity {
  Public = "public",
  Internal = "internal",
  Secret = "secret",
  Pii = "pii",
}

/** Lifecycle state of a memory. */
export enum MemoryStatus {
  Active = "active",
  Corrected = "corrected",
  Superseded = "superseded",
  Outdated = "outdated",
  Expired = "expired",
  Deleted = "deleted",
  Disputed = "disputed",
}

// ---------------------------------------------------------------------------
// Response models (camelCase mirrors of the REST response schemas)
// ---------------------------------------------------------------------------

/**
 * A memory with full provenance, lifecycle, and trust signals.
 *
 * Mirrors `MemoryResponse` (REST). Timestamps are ISO-8601 UTC strings as sent
 * over the wire.
 */
export interface Memory {
  memoryId: string;
  content: string;
  sourceType: SourceType;
  sourceRef: string;
  scope: Scope;
  scopeRef: string | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
  trustScore: number;
  sensitivity: Sensitivity;
  status: MemoryStatus;
  /** `memoryId`s of conflicting memories. */
  contradicts: string[];
  tags: string[];
  /** Extensible bag (connectors / enterprise + lineage pointers). */
  metadata?: Record<string, unknown>;
}

/**
 * One surfaced memory from a trust-aware query, with scores and audit reasons.
 *
 * Mirrors `RetrievedMemoryResponse` (REST).
 */
export interface RetrievedMemory {
  memory: Memory;
  relevance: number;
  finalRank: number;
  /** Human-readable reasons explaining why the memory was surfaced. */
  reasons: string[];
}

/**
 * The full query response: ranked results plus the audit-correlation id.
 *
 * Mirrors `QueryResponse` (REST). The {@link MemoryGuard.query} method returns
 * the `results` array directly; this type is provided for callers that need
 * the `queryId` correlation.
 */
export interface QueryResponse {
  results: RetrievedMemory[];
  /** Correlates the response to the audit record emitted for the query. */
  queryId: string;
}

/**
 * Result of a path ingestion run.
 *
 * Mirrors `IngestPathResponse` (REST).
 */
export interface IngestPathResult {
  created: number;
  memoryIds: string[];
}

/**
 * A contradiction link for a memory (the *contradicting* memory's details).
 *
 * Mirrors `ContradictionResponse` (REST).
 */
export interface Contradiction {
  /** Identifier of the contradicting memory (navigation/link target). */
  memoryId: string;
  /** Provenance of the contradicting memory, when available. */
  sourceRef?: string | null;
  /** Lifecycle status of the contradicting memory, when available. */
  status?: MemoryStatus | string | null;
  /** Human-readable reason the two memories conflict. */
  reason?: string | null;
  /** Detector confidence in [0, 1], when available. */
  confidence?: number | null;
}

// ---------------------------------------------------------------------------
// Request models (camelCase mirrors of the REST request schemas)
// ---------------------------------------------------------------------------

/**
 * Arguments for {@link MemoryGuard.add}.
 *
 * Mirrors `CreateMemoryRequest` (REST). `sourceType` and `sourceRef` are
 * required provenance; `sourceRef` must be non-empty.
 */
export interface AddMemoryRequest {
  content: string;
  sourceType: SourceType;
  sourceRef: string;
  scope: Scope;
  scopeRef?: string | null;
  sensitivity?: Sensitivity;
  expiresAt?: string | null;
  tags?: string[];
}

/**
 * Arguments for {@link MemoryGuard.query}.
 *
 * Mirrors `QueryRequest` (REST).
 */
export interface QueryRequest {
  text: string;
  scope?: Scope | null;
  scopeRef?: string | null;
  minTrust?: number;
  maxSensitivity?: Sensitivity;
  limit?: number;
}

/**
 * Arguments for {@link MemoryGuard.ingestPath}.
 *
 * Mirrors `IngestPathRequest` (REST).
 */
export interface IngestPathRequest {
  path: string;
  scope: Scope;
  scopeRef?: string | null;
}

// ---------------------------------------------------------------------------
// Client configuration + errors
// ---------------------------------------------------------------------------

/** Injectable `fetch` implementation (defaults to global `fetch`). */
export type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

/**
 * Options for {@link MemoryGuard.remote}.
 *
 * Requirement 12.1: the remote constructor targets the REST API base URL with
 * an optional authentication token.
 */
export interface RemoteOptions {
  /** Base URL of the MemoryGuard REST API (no trailing slash needed). */
  baseUrl: string;
  /** Optional bearer token sent as `Authorization: Bearer <token>`. */
  token?: string;
  /** Injectable fetch (defaults to the global `fetch`); handy for tests. */
  fetch?: FetchLike;
}

/** Raised when the REST API responds with a non-2xx status. */
export class MemoryGuardError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "MemoryGuardError";
    this.status = status;
    this.body = body;
  }
}

// ---------------------------------------------------------------------------
// Wire shapes (snake_case mirrors of the REST schemas in apps/api schemas.py)
//
// These are the exact JSON shapes sent to / received from the REST API. The
// converters below map between these and the camelCase SDK shapes above. They
// are exported so the serialization round-trip can be exercised directly
// (task 24.3) without going through the network.
// ---------------------------------------------------------------------------

/** Wire shape of `MemoryResponse` (snake_case). */
export interface MemoryWire {
  memory_id: string;
  content: string;
  source_type: string;
  source_ref: string;
  scope: string;
  scope_ref: string | null;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  trust_score: number;
  sensitivity: string;
  status: string;
  contradicts: string[];
  tags: string[];
  metadata?: Record<string, unknown>;
}

/** Wire shape of `RetrievedMemoryResponse` (snake_case). */
export interface RetrievedMemoryWire {
  memory: MemoryWire;
  relevance: number;
  final_rank: number;
  reasons: string[];
}

/** Wire shape of `QueryResponse` (snake_case). */
export interface QueryResponseWire {
  results: RetrievedMemoryWire[];
  query_id: string;
}

/** Wire shape of `IngestPathResponse` (snake_case). */
export interface IngestPathResponseWire {
  created: number;
  memory_ids: string[];
}

/** Wire shape of `ContradictionResponse` (snake_case). */
export interface ContradictionWire {
  memory_id: string;
  source_ref?: string | null;
  status?: string | null;
  reason?: string | null;
  confidence?: number | null;
}

/** Wire shape of `CreateMemoryRequest` (snake_case). */
export interface CreateMemoryWire {
  content: string;
  source_type: string;
  source_ref: string;
  scope: string;
  scope_ref?: string | null;
  sensitivity?: string;
  expires_at?: string | null;
  tags?: string[];
}

/** Wire shape of `QueryRequest` (snake_case). */
export interface QueryRequestWire {
  text: string;
  scope?: string | null;
  scope_ref?: string | null;
  min_trust?: number;
  max_sensitivity?: string;
  limit?: number;
}

/** Wire shape of `IngestPathRequest` (snake_case). */
export interface IngestPathRequestWire {
  path: string;
  scope: string;
  scope_ref?: string | null;
}

/** Wire shape of `UpdateMemoryRequest` (snake_case). */
export interface UpdateMemoryWire {
  content: string;
}

// ---------------------------------------------------------------------------
// Serialization (camelCase SDK shape -> snake_case wire shape)
// ---------------------------------------------------------------------------

/** Serialize {@link AddMemoryRequest} to the `POST /v1/memories` body. */
export function serializeAddRequest(request: AddMemoryRequest): CreateMemoryWire {
  const wire: CreateMemoryWire = {
    content: request.content,
    source_type: request.sourceType,
    source_ref: request.sourceRef,
    scope: request.scope,
  };
  if (request.scopeRef !== undefined) wire.scope_ref = request.scopeRef;
  if (request.sensitivity !== undefined) wire.sensitivity = request.sensitivity;
  if (request.expiresAt !== undefined) wire.expires_at = request.expiresAt;
  if (request.tags !== undefined) wire.tags = request.tags;
  return wire;
}

/** Serialize {@link QueryRequest} to the `POST /v1/query` body. */
export function serializeQueryRequest(request: QueryRequest): QueryRequestWire {
  const wire: QueryRequestWire = { text: request.text };
  if (request.scope !== undefined) wire.scope = request.scope;
  if (request.scopeRef !== undefined) wire.scope_ref = request.scopeRef;
  if (request.minTrust !== undefined) wire.min_trust = request.minTrust;
  if (request.maxSensitivity !== undefined) {
    wire.max_sensitivity = request.maxSensitivity;
  }
  if (request.limit !== undefined) wire.limit = request.limit;
  return wire;
}

/** Serialize {@link IngestPathRequest} to the `POST /v1/ingest/path` body. */
export function serializeIngestPathRequest(
  request: IngestPathRequest,
): IngestPathRequestWire {
  const wire: IngestPathRequestWire = {
    path: request.path,
    scope: request.scope,
  };
  if (request.scopeRef !== undefined) wire.scope_ref = request.scopeRef;
  return wire;
}

// ---------------------------------------------------------------------------
// Deserialization (snake_case wire shape -> camelCase SDK shape)
// ---------------------------------------------------------------------------

/** Deserialize a `MemoryResponse` wire object into a {@link Memory}. */
export function deserializeMemory(wire: MemoryWire): Memory {
  const memory: Memory = {
    memoryId: wire.memory_id,
    content: wire.content,
    sourceType: wire.source_type as SourceType,
    sourceRef: wire.source_ref,
    scope: wire.scope as Scope,
    scopeRef: wire.scope_ref ?? null,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    expiresAt: wire.expires_at ?? null,
    trustScore: wire.trust_score,
    sensitivity: wire.sensitivity as Sensitivity,
    status: wire.status as MemoryStatus,
    contradicts: wire.contradicts ?? [],
    tags: wire.tags ?? [],
  };
  if (wire.metadata !== undefined) memory.metadata = wire.metadata;
  return memory;
}

/**
 * Deserialize a `RetrievedMemoryResponse` wire object into a
 * {@link RetrievedMemory}.
 */
export function deserializeRetrievedMemory(
  wire: RetrievedMemoryWire,
): RetrievedMemory {
  return {
    memory: deserializeMemory(wire.memory),
    relevance: wire.relevance,
    finalRank: wire.final_rank,
    reasons: wire.reasons ?? [],
  };
}

/** Deserialize a `QueryResponse` wire object into a {@link QueryResponse}. */
export function deserializeQueryResponse(wire: QueryResponseWire): QueryResponse {
  return {
    results: (wire.results ?? []).map(deserializeRetrievedMemory),
    queryId: wire.query_id,
  };
}

/**
 * Deserialize an `IngestPathResponse` wire object into an
 * {@link IngestPathResult}.
 */
export function deserializeIngestPathResult(
  wire: IngestPathResponseWire,
): IngestPathResult {
  return {
    created: wire.created,
    memoryIds: wire.memory_ids ?? [],
  };
}

/**
 * Deserialize a `ContradictionResponse` wire object into a
 * {@link Contradiction}.
 */
export function deserializeContradiction(wire: ContradictionWire): Contradiction {
  return {
    memoryId: wire.memory_id,
    sourceRef: wire.source_ref ?? null,
    status: (wire.status as MemoryStatus | string | null | undefined) ?? null,
    reason: wire.reason ?? null,
    confidence: wire.confidence ?? null,
  };
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

/**
 * MemoryGuard TypeScript SDK client.
 *
 * Construct a remote client with {@link MemoryGuard.remote}:
 *
 * ```ts
 * import { MemoryGuard, Scope, SourceType, Sensitivity } from "@memoryguard/sdk";
 *
 * const mg = MemoryGuard.remote({ baseUrl: "http://localhost:8000", token });
 *
 * const mem = await mg.add({
 *   content: "billing-svc uses PostgreSQL 15",
 *   sourceType: SourceType.File,
 *   sourceRef: "repo://billing-svc/README.md@c4a1",
 *   scope: Scope.Repo,
 *   scopeRef: "billing-svc",
 *   sensitivity: Sensitivity.Internal,
 * });
 *
 * const results = await mg.query({
 *   text: "what db does billing use?",
 *   scope: Scope.Repo, scopeRef: "billing-svc", minTrust: 0.5, limit: 5,
 * });
 * results.forEach((r) => console.log(r.memory.content, r.memory.trustScore, r.reasons));
 * ```
 *
 * The constructor is private; use the {@link MemoryGuard.remote} factory.
 */
export class MemoryGuard {
  /** Base URL of the REST API, normalized without a trailing slash. */
  readonly baseUrl: string;
  /** Optional bearer token used for `Authorization` headers. */
  protected readonly token?: string;
  /** Resolved fetch implementation. */
  protected readonly fetchImpl: FetchLike;

  protected constructor(options: RemoteOptions) {
    if (!options.baseUrl) {
      throw new Error("MemoryGuard.remote requires a baseUrl");
    }
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.token = options.token;
    this.fetchImpl =
      options.fetch ?? ((input, init) => fetch(input, init));
  }

  /**
   * Create a remote client targeting a MemoryGuard REST API.
   *
   * Requirement 12.1: targets the REST API base URL with an optional auth
   * token.
   */
  static remote(options: RemoteOptions): MemoryGuard {
    return new MemoryGuard(options);
  }

  /**
   * Create a memory with the supplied provenance and lifecycle metadata.
   *
   * Maps to `POST /v1/memories` (`CreateMemoryRequest` -> `MemoryResponse`).
   */
  async add(request: AddMemoryRequest): Promise<Memory> {
    throw new Error("TODO: not implemented");
  }

  /**
   * Fetch a single memory by its identifier.
   *
   * Maps to `GET /v1/memories/{memory_id}` (-> `MemoryResponse`). A missing
   * memory surfaces as a {@link MemoryGuardError} with status `404`.
   */
  async get(memoryId: string): Promise<Memory> {
    throw new Error("TODO: not implemented");
  }

  /**
   * Run a trust-aware query and return the ranked, surfaced memories.
   *
   * Maps to `POST /v1/query` (`QueryRequest` -> `QueryResponse`); returns the
   * `results` array. Each result includes the memory's content, `trustScore`,
   * `sourceRef`, and `reasons` (Requirement 12.3).
   */
  async query(request: QueryRequest): Promise<RetrievedMemory[]> {
    throw new Error("TODO: not implemented");
  }

  /**
   * Ingest a local file, folder, or git repository into memory.
   *
   * Maps to `POST /v1/ingest/path` (`IngestPathRequest` ->
   * `IngestPathResponse`).
   */
  async ingestPath(request: IngestPathRequest): Promise<IngestPathResult> {
    throw new Error("TODO: not implemented");
  }

  /**
   * Correct a memory with new content, creating a corrected lineage.
   *
   * Maps to `PATCH /v1/memories/{memory_id}` (`UpdateMemoryRequest` ->
   * `MemoryResponse`). The prior record becomes `corrected`.
   */
  async correct(memoryId: string, content: string): Promise<Memory> {
    throw new Error("TODO: not implemented");
  }

  /**
   * Soft-delete a memory (sets `status` to `deleted`; record is preserved).
   *
   * Maps to `DELETE /v1/memories/{memory_id}`.
   */
  async delete(memoryId: string): Promise<void> {
    throw new Error("TODO: not implemented");
  }

  /**
   * List the contradictions recorded for a memory.
   *
   * Maps to `GET /v1/memories/{memory_id}/contradictions` (->
   * `ContradictionResponse[]`).
   */
  async contradictions(memoryId: string): Promise<Contradiction[]> {
    throw new Error("TODO: not implemented");
  }
}

/** Default export: the SDK entry point. */
export default MemoryGuard;
