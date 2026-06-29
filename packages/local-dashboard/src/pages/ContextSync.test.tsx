import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ContextSync from "./ContextSync";
import type { ContextSyncResponse, MemoryGuardApi, MemoryResponse } from "../lib/api";

const memory: MemoryResponse = {
  memory_id: "m_1",
  content: "Use pnpm for JavaScript commands",
  source_type: "user",
  source_ref: "user://me",
  scope: "project",
  scope_ref: "demo",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  expires_at: null,
  trust_score: 0.8,
  sensitivity: "internal",
  status: "active",
  contradicts: [],
  tags: [],
};

const contextState: ContextSyncResponse = {
  files: [
    { path: "AGENTS.md", exists: true, managed: true, size: 1200 },
    { path: "CLAUDE.md", exists: false, managed: false, size: 0 },
  ],
  last_sync_time: null,
  pending_diff: "--- a/AGENTS.md\n+++ b/AGENTS.md\n",
  pending_files: ["AGENTS.md"],
};

function fakeApi(overrides: Partial<MemoryGuardApi> = {}): MemoryGuardApi {
  return {
    baseUrl: "http://127.0.0.1:8000",
    listMemories: vi.fn(() => Promise.resolve([memory])),
    getMemory: vi.fn(() => Promise.resolve(memory)),
    getContradictions: vi.fn(() => Promise.resolve([])),
    runQuery: vi.fn(() => Promise.resolve({ results: [], query_id: "q_1" })),
    getHealth: vi.fn(() =>
      Promise.resolve({ status: "ok", mode: "local", flags: {} }),
    ),
    getProjects: vi.fn(() => Promise.resolve([])),
    getContextSync: vi.fn(() => Promise.resolve(contextState)),
    approveContextSync: vi.fn(() =>
      Promise.resolve({ ...contextState, pending_diff: "", pending_files: [] }),
    ),
    rejectContextSync: vi.fn(() =>
      Promise.resolve({ ...contextState, pending_diff: "", pending_files: [] }),
    ),
    ...overrides,
  };
}

describe("ContextSync", () => {
  it("shows generated files and the pending diff", async () => {
    render(<ContextSync api={fakeApi()} />);

    expect(await screen.findByText("AGENTS.md")).toBeInTheDocument();
    expect(screen.getByText("CLAUDE.md")).toBeInTheDocument();
    expect(screen.getByText(/--- a\/AGENTS.md/)).toBeInTheDocument();
  });

  it("approves pending diffs", async () => {
    const approveContextSync = vi.fn(() =>
      Promise.resolve({ ...contextState, pending_diff: "", pending_files: [] }),
    );
    render(<ContextSync api={fakeApi({ approveContextSync })} />);

    fireEvent.click(await screen.findByRole("button", { name: /approve/i }));

    await waitFor(() => expect(approveContextSync).toHaveBeenCalledTimes(1));
  });
});
