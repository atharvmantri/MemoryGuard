import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import MemoriesList from "./MemoriesList";
import type { MemoryGuardApi, MemoryResponse } from "../lib/api";

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
  contradicts: ["m_2"],
  tags: ["db"],
};

function fakeApi(overrides: Partial<MemoryGuardApi> = {}): MemoryGuardApi {
  return {
    baseUrl: "http://127.0.0.1:8000",
    listMemories: vi.fn(() => Promise.resolve([memory])),
    getMemory: vi.fn(() => Promise.resolve(memory)),
    getContradictions: vi.fn(() => Promise.resolve([])),
    runQuery: vi.fn(() =>
      Promise.resolve({ results: [], query_id: "q_1" }),
    ),
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

describe("MemoriesList (Requirements 15.1 / 15.2)", () => {
  it("renders a memory row with source, scope, trust meter, status and contradiction badge", async () => {
    render(<MemoriesList api={fakeApi()} />);

    expect(
      await screen.findByText(/billing-svc uses PostgreSQL 15/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("repo://billing-svc/README.md@c4a1"),
    ).toBeInTheDocument();
    expect(screen.getByText("repo: billing-svc")).toBeInTheDocument();
    expect(screen.getByRole("meter")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    // contradiction badge shows the count (1 unresolved contradiction)
    expect(
      screen.getByLabelText(/unresolved contradiction/),
    ).toBeInTheDocument();
  });

  it("invokes onSelect when a row is activated", async () => {
    const onSelect = vi.fn();
    render(<MemoriesList api={fakeApi()} onSelect={onSelect} />);

    const row = await screen.findByRole("button", {
      name: /Open memory m_1/,
    });
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith("m_1");
  });

  it("shows an empty state when there are no memories", async () => {
    render(
      <MemoriesList
        api={fakeApi({ listMemories: vi.fn(() => Promise.resolve([])) })}
      />,
    );
    expect(
      await screen.findByText(/No memories stored yet/),
    ).toBeInTheDocument();
  });

  it("surfaces an error message when the API fails", async () => {
    render(
      <MemoriesList
        api={fakeApi({
          listMemories: vi.fn(() => Promise.reject(new Error("boom"))),
        })}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/);
  });
});
