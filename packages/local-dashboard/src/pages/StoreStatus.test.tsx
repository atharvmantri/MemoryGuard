import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import StoreStatus from "./StoreStatus";
import type {
  HealthResponse,
  MemoryGuardApi,
  MemoryResponse,
} from "../lib/api";

const memory: MemoryResponse = {
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
  tags: [],
};

const health: HealthResponse = {
  status: "ok",
  mode: "local",
  version: "0.1.0",
  flags: { local_store: true, cloud_store: false },
};

function fakeApi(overrides: Partial<MemoryGuardApi> = {}): MemoryGuardApi {
  return {
    baseUrl: "http://127.0.0.1:8000",
    listMemories: vi.fn(() => Promise.resolve([memory, memory, memory])),
    getMemory: vi.fn(() => Promise.resolve(memory)),
    getContradictions: vi.fn(() => Promise.resolve([])),
    runQuery: vi.fn(() => Promise.resolve({ results: [], query_id: "q_1" })),
    getHealth: vi.fn(() => Promise.resolve(health)),
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

describe("StoreStatus (Requirement 15.4)", () => {
  it("shows memory counts, mode = local, health and version", async () => {
    render(<StoreStatus api={fakeApi()} />);

    // Memory count derived from listMemories length.
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("Memories")).toBeInTheDocument();

    // Mode = local (Req 15.4).
    expect(screen.getByText("local")).toBeInTheDocument();
    expect(screen.getByText("Mode")).toBeInTheDocument();

    // Health + version.
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
  });

  it("renders active feature flags with on/off state", async () => {
    render(<StoreStatus api={fakeApi()} />);

    expect(await screen.findByText("local_store")).toBeInTheDocument();
    expect(screen.getByText("cloud_store")).toBeInTheDocument();

    const localStore = screen
      .getByText("local_store")
      .closest(".mg-flag") as HTMLElement;
    expect(localStore).toHaveAttribute("data-enabled", "true");

    const cloudStore = screen
      .getByText("cloud_store")
      .closest(".mg-flag") as HTMLElement;
    expect(cloudStore).toHaveAttribute("data-enabled", "false");
  });

  it("reloads status when Refresh is clicked", async () => {
    const getHealth = vi.fn(() => Promise.resolve(health));
    const listMemories = vi.fn(() => Promise.resolve([memory]));
    render(<StoreStatus api={fakeApi({ getHealth, listMemories })} />);

    await screen.findByText("1");
    expect(getHealth).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(getHealth).toHaveBeenCalledTimes(2));
    expect(listMemories).toHaveBeenCalledTimes(2);
  });

  it("surfaces an error when status cannot be loaded", async () => {
    render(
      <StoreStatus
        api={fakeApi({
          getHealth: vi.fn(() => Promise.reject(new Error("offline"))),
        })}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/offline/);
  });
});
