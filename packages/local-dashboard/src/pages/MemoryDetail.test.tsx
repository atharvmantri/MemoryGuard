import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import MemoryDetail from "./MemoryDetail";
import type {
  ContradictionEntry,
  MemoryGuardApi,
  MemoryResponse,
} from "../lib/api";

const memory: MemoryResponse = {
  memory_id: "m_1",
  content: "billing-svc uses PostgreSQL 15 for its primary datastore",
  source_type: "commit",
  source_ref: "repo://billing-svc/README.md@c4a1",
  scope: "repo",
  scope_ref: "billing-svc",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-02T00:00:00Z",
  expires_at: null,
  trust_score: 0.82,
  sensitivity: "internal",
  status: "disputed",
  contradicts: ["m_2"],
  tags: ["db"],
  // Lineage pointers live in the extensible metadata bag when the API exposes it.
  metadata: { supersedes: "m_0", superseded_by: ["m_9"] },
};

const contradiction: ContradictionEntry = {
  memory_id: "m_2",
  source_ref: "repo://billing-svc/NOTES.md@d9",
  status: "active",
  reason: "numeric_conflict",
  confidence: 0.8,
};

function fakeApi(overrides: Partial<MemoryGuardApi> = {}): MemoryGuardApi {
  return {
    baseUrl: "http://127.0.0.1:8000",
    listMemories: vi.fn(() => Promise.resolve([memory])),
    getMemory: vi.fn(() => Promise.resolve(memory)),
    getContradictions: vi.fn(() => Promise.resolve([contradiction])),
    runQuery: vi.fn(() => Promise.resolve({ results: [], query_id: "q_1" })),
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

describe("MemoryDetail (Requirement 15.2)", () => {
  it("renders full provenance and the trust signal breakdown", async () => {
    render(<MemoryDetail memoryId="m_1" api={fakeApi()} />);

    expect(
      await screen.findByText(/billing-svc uses PostgreSQL 15/),
    ).toBeInTheDocument();
    // Provenance
    expect(
      screen.getByText("repo://billing-svc/README.md@c4a1"),
    ).toBeInTheDocument();
    expect(screen.getByText("repo: billing-svc")).toBeInTheDocument();
    // Trust breakdown (meter + numeric score)
    expect(screen.getByRole("meter")).toBeInTheDocument();
    expect(screen.getByText("0.82")).toBeInTheDocument();
  });

  it("links contradictions to the contradicting memory id and navigates", async () => {
    const onSelect = vi.fn();
    render(<MemoryDetail memoryId="m_1" api={fakeApi()} onSelect={onSelect} />);

    const link = await screen.findByRole("button", { name: "m_2" });
    expect(screen.getByText(/numeric_conflict/)).toBeInTheDocument();
    fireEvent.click(link);
    expect(onSelect).toHaveBeenCalledWith("m_2");
  });

  it("shows lineage from metadata and navigates to lineage ids", async () => {
    const onSelect = vi.fn();
    render(<MemoryDetail memoryId="m_1" api={fakeApi()} onSelect={onSelect} />);

    expect(await screen.findByText("Supersedes")).toBeInTheDocument();
    expect(screen.getByText("Superseded by")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "m_0" }));
    expect(onSelect).toHaveBeenCalledWith("m_0");
  });

  it("surfaces an error message when the API fails", async () => {
    render(
      <MemoryDetail
        memoryId="m_1"
        api={fakeApi({
          getMemory: vi.fn(() => Promise.reject(new Error("boom"))),
        })}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });
});
