import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import QueryPlayground from "./QueryPlayground";
import type {
  MemoryGuardApi,
  MemoryResponse,
  QueryResponse,
} from "../lib/api";

const memory: MemoryResponse = {
  memory_id: "m_1",
  content:
    "billing-svc uses PostgreSQL 15 for its primary transactional datastore",
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

const queryResult: QueryResponse = {
  query_id: "q_42",
  results: [
    {
      memory,
      relevance: 0.91,
      final_rank: 0.88,
      reasons: ["semantic match on 'database'", "trust 0.87 ≥ floor 0.50"],
    },
  ],
};

function fakeApi(overrides: Partial<MemoryGuardApi> = {}): MemoryGuardApi {
  return {
    baseUrl: "http://127.0.0.1:8000",
    listMemories: vi.fn(() => Promise.resolve([memory])),
    getMemory: vi.fn(() => Promise.resolve(memory)),
    getContradictions: vi.fn(() => Promise.resolve([])),
    runQuery: vi.fn(() => Promise.resolve(queryResult)),
    getHealth: vi.fn(() =>
      Promise.resolve({ status: "ok", mode: "local", flags: {} }),
    ),
    getProjects: vi.fn(() => Promise.resolve([])),
    getContextSync: vi.fn(() =>
      Promise.resolve({
        files: [],
        last_sync_time: null,
        pending_diff: "",
        pending_files: [],
      }),
    ),
    approveContextSync: vi.fn(() =>
      Promise.resolve({
        files: [],
        last_sync_time: null,
        pending_diff: "",
        pending_files: [],
      }),
    ),
    rejectContextSync: vi.fn(() =>
      Promise.resolve({
        files: [],
        last_sync_time: null,
        pending_diff: "",
        pending_files: [],
      }),
    ),
    ...overrides,
  };
}

describe("QueryPlayground (Requirement 15.3)", () => {
  it("runs a trust-aware query and shows per-result reasons + trust meter", async () => {
    const runQuery = vi.fn(() => Promise.resolve(queryResult));
    render(<QueryPlayground api={fakeApi({ runQuery })} />);

    fireEvent.change(screen.getByPlaceholderText(/what database/i), {
      target: { value: "what database does billing use?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run query/i }));

    // Result content + provenance render.
    expect(
      await screen.findByText(/billing-svc uses PostgreSQL 15/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("repo://billing-svc/README.md@c4a1"),
    ).toBeInTheDocument();

    // Trust meter (Req 15.3 trust-aware) is present.
    expect(screen.getByRole("meter")).toBeInTheDocument();

    // Per-result reasons (Req 15.3) are rendered.
    expect(
      screen.getByText(/semantic match on 'database'/),
    ).toBeInTheDocument();
    expect(screen.getByText(/trust 0.87 ≥ floor 0.50/)).toBeInTheDocument();

    // query_id correlation is surfaced.
    expect(screen.getByText(/query_id: q_42/)).toBeInTheDocument();

    // The query text was sent to the API.
    expect(runQuery).toHaveBeenCalledWith(
      expect.objectContaining({ text: "what database does billing use?" }),
    );
  });

  it("passes scope, scope_ref, min_trust and limit filters to the query", async () => {
    const runQuery = vi.fn(() => Promise.resolve(queryResult));
    render(<QueryPlayground api={fakeApi({ runQuery })} />);

    fireEvent.change(screen.getByPlaceholderText(/what database/i), {
      target: { value: "db" },
    });
    fireEvent.change(screen.getByDisplayValue("any"), {
      target: { value: "repo" },
    });
    fireEvent.change(screen.getByPlaceholderText("billing-svc"), {
      target: { value: "billing-svc" },
    });

    fireEvent.click(screen.getByRole("button", { name: /run query/i }));

    await waitFor(() => expect(runQuery).toHaveBeenCalled());
    expect(runQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "db",
        scope: "repo",
        scope_ref: "billing-svc",
        min_trust: 0,
        limit: 10,
      }),
    );
  });

  it("calls onSelect when a result title is clicked", async () => {
    const onSelect = vi.fn();
    render(<QueryPlayground api={fakeApi()} onSelect={onSelect} />);

    fireEvent.change(screen.getByPlaceholderText(/what database/i), {
      target: { value: "db" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run query/i }));

    const title = await screen.findByText(/billing-svc uses PostgreSQL 15/);
    fireEvent.click(title);
    expect(onSelect).toHaveBeenCalledWith("m_1");
  });

  it("validates empty query text without calling the API", async () => {
    const runQuery = vi.fn(() => Promise.resolve(queryResult));
    render(<QueryPlayground api={fakeApi({ runQuery })} />);

    fireEvent.click(screen.getByRole("button", { name: /run query/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /enter query text/i,
    );
    expect(runQuery).not.toHaveBeenCalled();
  });

  it("shows an empty state when no trusted memories match", async () => {
    render(
      <QueryPlayground
        api={fakeApi({
          runQuery: vi.fn(() =>
            Promise.resolve({ results: [], query_id: "q_0" }),
          ),
        })}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/what database/i), {
      target: { value: "nothing matches" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run query/i }));

    expect(
      await screen.findByText(/No trusted memories matched/i),
    ).toBeInTheDocument();
  });

  it("surfaces an error when the query fails", async () => {
    render(
      <QueryPlayground
        api={fakeApi({
          runQuery: vi.fn(() => Promise.reject(new Error("boom"))),
        })}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/what database/i), {
      target: { value: "db" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run query/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });
});
