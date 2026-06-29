import { describe, it, expect, vi } from "vitest";
import {
  ApiError,
  createApiClient,
  DEFAULT_BASE_URL,
  type MemoryResponse,
  type QueryResponse,
} from "./api";

/** Build a fake fetch that returns the given body/status and records calls. */
function fakeFetch(
  body: unknown,
  init: { status?: number; ok?: boolean } = {},
) {
  const status = init.status ?? 200;
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn((url: string, reqInit?: RequestInit) => {
    calls.push({ url, init: reqInit });
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return Promise.resolve(
      new Response(text, {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  return { fn, calls };
}

const sampleMemory: MemoryResponse = {
  memory_id: "m_1",
  content: "billing-svc uses PostgreSQL 15",
  source_type: "commit",
  source_ref: "repo://billing-svc/README.md@c4a1",
  scope: "repo",
  scope_ref: "billing-svc",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-02T00:00:00Z",
  expires_at: null,
  trust_score: 0.87,
  sensitivity: "internal",
  status: "active",
  contradicts: [],
  tags: ["db"],
};

describe("createApiClient base URL handling", () => {
  it("defaults to the local engine loopback address", () => {
    expect(createApiClient().baseUrl).toBe(DEFAULT_BASE_URL);
  });

  it("trims trailing slashes from a custom base URL", () => {
    const client = createApiClient({ baseUrl: "http://localhost:9000/" });
    expect(client.baseUrl).toBe("http://localhost:9000");
  });
});

describe("listMemories", () => {
  it("parses a bare array response", async () => {
    const { fn, calls } = fakeFetch([sampleMemory]);
    const client = createApiClient({ baseUrl: DEFAULT_BASE_URL, fetch: fn });
    const result = await client.listMemories();
    expect(result).toHaveLength(1);
    expect(result[0].memory_id).toBe("m_1");
    expect(calls[0].url).toBe(`${DEFAULT_BASE_URL}/v1/memories`);
  });

  it("parses an object-wrapped response and forwards filters", async () => {
    const { fn, calls } = fakeFetch({ memories: [sampleMemory] });
    const client = createApiClient({ fetch: fn });
    const result = await client.listMemories({
      scope: "repo",
      scope_ref: "billing-svc",
      status: "active",
    });
    expect(result).toHaveLength(1);
    expect(calls[0].url).toContain("scope=repo");
    expect(calls[0].url).toContain("scope_ref=billing-svc");
    expect(calls[0].url).toContain("status=active");
  });
});

describe("getMemory", () => {
  it("returns a typed memory and URL-encodes the id", async () => {
    const { fn, calls } = fakeFetch(sampleMemory);
    const client = createApiClient({ baseUrl: DEFAULT_BASE_URL, fetch: fn });
    const result = await client.getMemory("m/1");
    expect(result.trust_score).toBe(0.87);
    expect(calls[0].url).toBe(`${DEFAULT_BASE_URL}/v1/memories/m%2F1`);
  });
});

describe("getContradictions", () => {
  it("normalizes a wrapped contradictions payload", async () => {
    const { fn } = fakeFetch({
      contradictions: [
        {
          memory_id: "m_2",
          source_ref: "repo://billing-svc/NOTES.md@d9",
          status: "disputed",
          reason: "numeric_conflict",
          confidence: 0.82,
        },
      ],
    });
    const client = createApiClient({ fetch: fn });
    const result = await client.getContradictions("m_1");
    expect(result).toHaveLength(1);
    expect(result[0].memory_id).toBe("m_2");
    expect(result[0].reason).toBe("numeric_conflict");
    expect(result[0].confidence).toBe(0.82);
  });

  it("parses a bare array of contradiction entries", async () => {
    const { fn } = fakeFetch([
      { memory_id: "m_3", status: "active", reason: "negation" },
    ]);
    const client = createApiClient({ fetch: fn });
    const result = await client.getContradictions("m_1");
    expect(result).toHaveLength(1);
    expect(result[0].memory_id).toBe("m_3");
  });
});

describe("runQuery", () => {
  it("POSTs the query body and parses the response", async () => {
    const response: QueryResponse = {
      query_id: "q_8f",
      results: [
        {
          memory: sampleMemory,
          relevance: 0.91,
          final_rank: 0.89,
          reasons: ["semantic match", "fresh"],
        },
      ],
    };
    const { fn, calls } = fakeFetch(response);
    const client = createApiClient({ fetch: fn });
    const result = await client.runQuery({
      text: "what db?",
      scope: "repo",
      min_trust: 0.5,
      limit: 5,
    });
    expect(result.query_id).toBe("q_8f");
    expect(result.results[0].reasons).toContain("semantic match");
    expect(calls[0].init?.method).toBe("POST");
    const sentBody = JSON.parse(String(calls[0].init?.body));
    expect(sentBody).toMatchObject({ text: "what db?", min_trust: 0.5, limit: 5 });
  });
});

describe("getHealth", () => {
  it("parses status, mode, and flags", async () => {
    const { fn } = fakeFetch({
      status: "ok",
      mode: "local",
      version: "0.1.0",
      flags: { cloud_auth: false, local_embedder: true },
    });
    const client = createApiClient({ fetch: fn });
    const health = await client.getHealth();
    expect(health.mode).toBe("local");
    expect(health.flags.local_embedder).toBe(true);
    expect(health.flags.cloud_auth).toBe(false);
  });
});

describe("error handling", () => {
  it("throws ApiError carrying the status and body for non-2xx", async () => {
    const { fn } = fakeFetch(
      { error: "feature_not_enabled", todo: "enterprise" },
      { status: 501 },
    );
    const client = createApiClient({ fetch: fn });
    await expect(client.getMemory("missing")).rejects.toMatchObject({
      name: "ApiError",
      status: 501,
    });
  });

  it("wraps network failures as ApiError with status 0", async () => {
    const fn = vi.fn(() => Promise.reject(new Error("connection refused")));
    const client = createApiClient({ fetch: fn });
    const error = await client.getHealth().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
  });
});
