/**
 * Property-based tests for the TypeScript SDK serialization round-trip.
 *
 * Task 24.3 — fast-check property tests asserting that the SDK's
 * serialization layer (camelCase SDK shapes <-> snake_case REST wire shapes)
 * is information-preserving across the client/server boundary. This mirrors
 * **Property 20 (Round-trip fidelity)** from the core store at the SDK level:
 * what a caller sends is faithfully reconstructed from what the server returns.
 *
 * The two directions exercised are the actual exported SDK functions:
 *   - request path:  serialize{Add,Query,IngestPath}Request (camel -> snake)
 *   - response path: deserialize{Memory,RetrievedMemory,QueryResponse,
 *                    IngestPathResult,Contradiction} (snake -> camel)
 *
 * Wire objects are passed through `JSON.parse(JSON.stringify(...))` to emulate
 * real REST transport (the remote client serializes the body and parses
 * `response.json()`), so the properties also guard against any field that
 * fails to survive JSON encoding.
 *
 * **Validates: Requirements 12.3, 12.4**
 */

import { describe, expect, it } from "vitest";
import fc from "fast-check";

import {
  // enums
  SourceType,
  Scope,
  Sensitivity,
  MemoryStatus,
  // request models + serializers
  AddMemoryRequest,
  QueryRequest,
  IngestPathRequest,
  serializeAddRequest,
  serializeQueryRequest,
  serializeIngestPathRequest,
  // wire shapes
  MemoryWire,
  RetrievedMemoryWire,
  QueryResponseWire,
  IngestPathResponseWire,
  ContradictionWire,
  // response deserializers
  deserializeMemory,
  deserializeRetrievedMemory,
  deserializeQueryResponse,
  deserializeIngestPathResult,
  deserializeContradiction,
} from "./index.js";

// ---------------------------------------------------------------------------
// Helpers + generators
// ---------------------------------------------------------------------------

/** Emulate REST transport: encode to JSON and parse back, like a real client. */
function overTheWire<T>(wire: T): T {
  return JSON.parse(JSON.stringify(wire)) as T;
}

/** Pick any value from a TypeScript string enum. */
function enumArb<E extends Record<string, string>>(
  e: E,
): fc.Arbitrary<E[keyof E]> {
  return fc.constantFrom(...(Object.values(e) as E[keyof E][]));
}

/** A finite, JSON-safe number in [0, 1] (trust/relevance/confidence). */
const unitArb = fc.double({
  min: 0,
  max: 1,
  noNaN: true,
  noDefaultInfinity: true,
});

/** A finite, JSON-safe non-negative score (final_rank can exceed 1). */
const scoreArb = fc.double({
  min: 0,
  max: 1_000_000,
  noNaN: true,
  noDefaultInfinity: true,
});

/** ISO-8601 UTC timestamp string, as sent over the wire. */
const isoArb = fc
  .integer({ min: 0, max: 4_102_444_800_000 }) // 1970-01-01 .. 2100-01-01
  .map((ms) => new Date(ms).toISOString());

const stringArb = fc.string();
const tagsArb = fc.array(fc.string(), { maxLength: 6 });
const reasonsArb = fc.array(fc.string(), { maxLength: 6 });

/** Arbitrary {@link AddMemoryRequest} with all optional fields populated. */
const addRequestArb: fc.Arbitrary<AddMemoryRequest> = fc.record({
  content: stringArb,
  sourceType: enumArb(SourceType),
  sourceRef: fc.string({ minLength: 1 }),
  scope: enumArb(Scope),
  scopeRef: fc.oneof(fc.string(), fc.constant(null)),
  sensitivity: enumArb(Sensitivity),
  expiresAt: fc.oneof(isoArb, fc.constant(null)),
  tags: tagsArb,
});

/** Arbitrary {@link QueryRequest} with all optional fields populated. */
const queryRequestArb: fc.Arbitrary<QueryRequest> = fc.record({
  text: stringArb,
  scope: fc.oneof(enumArb(Scope), fc.constant(null)),
  scopeRef: fc.oneof(fc.string(), fc.constant(null)),
  minTrust: unitArb,
  maxSensitivity: enumArb(Sensitivity),
  limit: fc.integer({ min: 1, max: 1000 }),
});

/** Arbitrary {@link IngestPathRequest}. */
const ingestPathRequestArb: fc.Arbitrary<IngestPathRequest> = fc.record({
  path: fc.string({ minLength: 1 }),
  scope: enumArb(Scope),
  scopeRef: fc.oneof(fc.string(), fc.constant(null)),
});

/** Arbitrary `MemoryResponse` wire object (snake_case). */
const memoryWireArb: fc.Arbitrary<MemoryWire> = fc.record({
  memory_id: fc.uuid(),
  content: stringArb,
  source_type: enumArb(SourceType),
  source_ref: fc.string({ minLength: 1 }),
  scope: enumArb(Scope),
  scope_ref: fc.oneof(fc.string(), fc.constant(null)),
  created_at: isoArb,
  updated_at: isoArb,
  expires_at: fc.oneof(isoArb, fc.constant(null)),
  trust_score: unitArb,
  sensitivity: enumArb(Sensitivity),
  status: enumArb(MemoryStatus),
  contradicts: fc.array(fc.uuid(), { maxLength: 4 }),
  tags: tagsArb,
});

/** Arbitrary `RetrievedMemoryResponse` wire object (snake_case). */
const retrievedWireArb: fc.Arbitrary<RetrievedMemoryWire> = fc.record({
  memory: memoryWireArb,
  relevance: unitArb,
  final_rank: scoreArb,
  reasons: reasonsArb,
});

/** Arbitrary `QueryResponse` wire object (snake_case). */
const queryResponseWireArb: fc.Arbitrary<QueryResponseWire> = fc.record({
  results: fc.array(retrievedWireArb, { maxLength: 5 }),
  query_id: fc.uuid(),
});

/** Arbitrary `IngestPathResponse` wire object (snake_case). */
const ingestResponseWireArb: fc.Arbitrary<IngestPathResponseWire> = fc.record({
  created: fc.integer({ min: 0, max: 10_000 }),
  memory_ids: fc.array(fc.uuid(), { maxLength: 8 }),
});

/** Arbitrary `ContradictionResponse` wire object (snake_case). */
const contradictionWireArb: fc.Arbitrary<ContradictionWire> = fc.record({
  memory_id: fc.uuid(),
  source_ref: fc.oneof(fc.string(), fc.constant(null)),
  status: fc.oneof(enumArb(MemoryStatus), fc.constant(null)),
  reason: fc.oneof(fc.string(), fc.constant(null)),
  confidence: fc.oneof(unitArb, fc.constant(null)),
});

// ---------------------------------------------------------------------------
// Headline round-trip: content + trustScore + sourceRef + reasons
// ---------------------------------------------------------------------------

describe("SDK serialization round-trip (Property 20 at the SDK boundary)", () => {
  it("preserves content, trustScore, sourceRef, and reasons across the full client->server->client trip", () => {
    fc.assert(
      fc.property(
        addRequestArb,
        unitArb,
        scoreArb,
        reasonsArb,
        fc.uuid(),
        (req, serverTrust, serverRank, serverReasons, queryId) => {
          // 1. Client serializes the typed request to the wire body (camel -> snake).
          const createWire = serializeAddRequest(req);

          // 2. The server stores it and later surfaces it from a query. A faithful
          //    server echoes the create fields verbatim and attaches its own
          //    trust/lifecycle fields. We only copy the snake_case fields the
          //    SDK serializer produced — the field-name/enum mapping under test.
          const memWire: MemoryWire = {
            memory_id: "11111111-1111-4111-8111-111111111111",
            content: createWire.content,
            source_type: createWire.source_type,
            source_ref: createWire.source_ref,
            scope: createWire.scope,
            scope_ref: createWire.scope_ref ?? null,
            created_at: "2024-01-01T00:00:00.000Z",
            updated_at: "2024-01-01T00:00:00.000Z",
            expires_at: createWire.expires_at ?? null,
            trust_score: serverTrust,
            sensitivity: createWire.sensitivity ?? Sensitivity.Internal,
            status: MemoryStatus.Active,
            contradicts: [],
            tags: createWire.tags ?? [],
          };
          const responseWire: QueryResponseWire = {
            results: [
              { memory: memWire, relevance: 1, final_rank: serverRank, reasons: serverReasons },
            ],
            query_id: queryId,
          };

          // 3. Client deserializes the response (snake -> camel).
          const response = deserializeQueryResponse(overTheWire(responseWire));
          const result = response.results[0];

          // content + sourceRef survive the request serialization;
          // trustScore + reasons survive the response deserialization.
          expect(result.memory.content).toBe(req.content);
          expect(result.memory.sourceRef).toBe(req.sourceRef);
          expect(result.memory.trustScore).toBe(serverTrust);
          expect(result.reasons).toEqual(serverReasons);
          expect(response.queryId).toBe(queryId);
        },
      ),
    );
  });
});

// ---------------------------------------------------------------------------
// Request serialization fidelity (camelCase -> snake_case wire)
// ---------------------------------------------------------------------------

describe("request serialization preserves all fields onto the wire", () => {
  it("serializeAddRequest maps every field to its snake_case wire key", () => {
    fc.assert(
      fc.property(addRequestArb, (req) => {
        const wire = overTheWire(serializeAddRequest(req));
        expect(wire.content).toBe(req.content);
        expect(wire.source_type).toBe(req.sourceType);
        expect(wire.source_ref).toBe(req.sourceRef);
        expect(wire.scope).toBe(req.scope);
        expect(wire.scope_ref).toBe(req.scopeRef);
        expect(wire.sensitivity).toBe(req.sensitivity);
        expect(wire.expires_at).toBe(req.expiresAt);
        expect(wire.tags).toEqual(req.tags);
      }),
    );
  });

  it("serializeQueryRequest maps every field to its snake_case wire key", () => {
    fc.assert(
      fc.property(queryRequestArb, (req) => {
        const wire = overTheWire(serializeQueryRequest(req));
        expect(wire.text).toBe(req.text);
        expect(wire.scope).toBe(req.scope);
        expect(wire.scope_ref).toBe(req.scopeRef);
        expect(wire.min_trust).toBe(req.minTrust);
        expect(wire.max_sensitivity).toBe(req.maxSensitivity);
        expect(wire.limit).toBe(req.limit);
      }),
    );
  });

  it("serializeIngestPathRequest maps every field to its snake_case wire key", () => {
    fc.assert(
      fc.property(ingestPathRequestArb, (req) => {
        const wire = overTheWire(serializeIngestPathRequest(req));
        expect(wire.path).toBe(req.path);
        expect(wire.scope).toBe(req.scope);
        expect(wire.scope_ref).toBe(req.scopeRef);
      }),
    );
  });

  it("omits optional request fields that are left undefined", () => {
    const wire = serializeAddRequest({
      content: "c",
      sourceType: SourceType.User,
      sourceRef: "ref",
      scope: Scope.Global,
    });
    expect("scope_ref" in wire).toBe(false);
    expect("sensitivity" in wire).toBe(false);
    expect("expires_at" in wire).toBe(false);
    expect("tags" in wire).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Response deserialization fidelity (snake_case wire -> camelCase)
// ---------------------------------------------------------------------------

describe("response deserialization preserves all fields from the wire", () => {
  it("deserializeMemory reconstructs every field (incl. content, trustScore, sourceRef)", () => {
    fc.assert(
      fc.property(memoryWireArb, (wire) => {
        const mem = deserializeMemory(overTheWire(wire));
        expect(mem.memoryId).toBe(wire.memory_id);
        expect(mem.content).toBe(wire.content);
        expect(mem.sourceType).toBe(wire.source_type);
        expect(mem.sourceRef).toBe(wire.source_ref);
        expect(mem.scope).toBe(wire.scope);
        expect(mem.scopeRef).toBe(wire.scope_ref);
        expect(mem.createdAt).toBe(wire.created_at);
        expect(mem.updatedAt).toBe(wire.updated_at);
        expect(mem.expiresAt).toBe(wire.expires_at);
        expect(mem.trustScore).toBe(wire.trust_score);
        expect(mem.sensitivity).toBe(wire.sensitivity);
        expect(mem.status).toBe(wire.status);
        expect(mem.contradicts).toEqual(wire.contradicts);
        expect(mem.tags).toEqual(wire.tags);
      }),
    );
  });

  it("deserializeRetrievedMemory preserves reasons, scores, and the nested memory", () => {
    fc.assert(
      fc.property(retrievedWireArb, (wire) => {
        const r = deserializeRetrievedMemory(overTheWire(wire));
        expect(r.relevance).toBe(wire.relevance);
        expect(r.finalRank).toBe(wire.final_rank);
        expect(r.reasons).toEqual(wire.reasons);
        expect(r.memory.content).toBe(wire.memory.content);
        expect(r.memory.trustScore).toBe(wire.memory.trust_score);
        expect(r.memory.sourceRef).toBe(wire.memory.source_ref);
      }),
    );
  });

  it("deserializeQueryResponse preserves queryId and every per-result reasons/trustScore/sourceRef", () => {
    fc.assert(
      fc.property(queryResponseWireArb, (wire) => {
        const resp = deserializeQueryResponse(overTheWire(wire));
        expect(resp.queryId).toBe(wire.query_id);
        expect(resp.results).toHaveLength(wire.results.length);
        resp.results.forEach((r, i) => {
          const w = wire.results[i];
          expect(r.reasons).toEqual(w.reasons);
          expect(r.memory.content).toBe(w.memory.content);
          expect(r.memory.trustScore).toBe(w.memory.trust_score);
          expect(r.memory.sourceRef).toBe(w.memory.source_ref);
        });
      }),
    );
  });

  it("deserializeIngestPathResult preserves created count and memoryIds", () => {
    fc.assert(
      fc.property(ingestResponseWireArb, (wire) => {
        const res = deserializeIngestPathResult(overTheWire(wire));
        expect(res.created).toBe(wire.created);
        expect(res.memoryIds).toEqual(wire.memory_ids);
      }),
    );
  });

  it("deserializeContradiction preserves memoryId, sourceRef, status, reason, confidence", () => {
    fc.assert(
      fc.property(contradictionWireArb, (wire) => {
        const c = deserializeContradiction(overTheWire(wire));
        expect(c.memoryId).toBe(wire.memory_id);
        expect(c.sourceRef).toBe(wire.source_ref ?? null);
        expect(c.status).toBe(wire.status ?? null);
        expect(c.reason).toBe(wire.reason ?? null);
        expect(c.confidence).toBe(wire.confidence ?? null);
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// Concrete example (unit) test — a single readable end-to-end round-trip
// ---------------------------------------------------------------------------

describe("serialization round-trip — concrete example", () => {
  it("round-trips the design's billing-svc memory through serialize + deserialize", () => {
    const req: AddMemoryRequest = {
      content: "billing-svc uses PostgreSQL 15",
      sourceType: SourceType.File,
      sourceRef: "repo://billing-svc/README.md@c4a1",
      scope: Scope.Repo,
      scopeRef: "billing-svc",
      sensitivity: Sensitivity.Internal,
      tags: ["db", "infra"],
    };

    const createWire = serializeAddRequest(req);
    expect(createWire.source_ref).toBe("repo://billing-svc/README.md@c4a1");
    expect(createWire.source_type).toBe("file");
    expect(createWire.scope).toBe("repo");

    const memWire: MemoryWire = {
      memory_id: "11111111-1111-4111-8111-111111111111",
      content: createWire.content,
      source_type: createWire.source_type,
      source_ref: createWire.source_ref,
      scope: createWire.scope,
      scope_ref: createWire.scope_ref ?? null,
      created_at: "2024-01-01T00:00:00.000Z",
      updated_at: "2024-01-01T00:00:00.000Z",
      expires_at: null,
      trust_score: 0.82,
      sensitivity: createWire.sensitivity ?? Sensitivity.Internal,
      status: "active",
      contradicts: [],
      tags: createWire.tags ?? [],
    };

    const responseWire: QueryResponseWire = {
      results: [
        {
          memory: memWire,
          relevance: 0.9,
          final_rank: 0.88,
          reasons: ["high source authority", "recent"],
        },
      ],
      query_id: "22222222-2222-4222-8222-222222222222",
    };

    const response = deserializeQueryResponse(overTheWire(responseWire));
    const result = response.results[0];

    expect(result.memory.content).toBe("billing-svc uses PostgreSQL 15");
    expect(result.memory.sourceRef).toBe("repo://billing-svc/README.md@c4a1");
    expect(result.memory.sourceType).toBe(SourceType.File);
    expect(result.memory.scope).toBe(Scope.Repo);
    expect(result.memory.trustScore).toBe(0.82);
    expect(result.reasons).toEqual(["high source authority", "recent"]);
    expect(response.queryId).toBe("22222222-2222-4222-8222-222222222222");
  });
});
