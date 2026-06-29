# MemoryGuard examples

Runnable examples for the MemoryGuard **Phase 1** developer flow. Every example
runs **entirely locally with no external LLM API** — the only model used is the
on-device embedder, and no commercial module or cloud service is required.

Each example is self-contained and launches the same way:

```bash
uv run python examples/<name>/main.py
```

## Index

| Example | What it shows | Run |
| --- | --- | --- |
| [`quickstart/`](./quickstart) | The full flow in one script: build a local store, add memories (one contradicts another), run a trust-aware query with provenance + trust + reasons, show a contradiction, and explain a memory. | `uv run python examples/quickstart/main.py` |
| [`repo-ingest/`](./repo-ingest) | Ingest a folder of documents (one memory per chunk, with file provenance) and query it back. Mirrors `memoryguard ingest`. | `uv run python examples/repo-ingest/main.py` |
| [`mcp-agent/`](./mcp-agent) | Register the MemoryGuard MCP server in an AI coding agent (example `mcpServers` JSON), and call the MCP tool handlers (`tool_memory_add`, `tool_memory_search`) directly against a local engine. | `uv run python examples/mcp-agent/main.py` |
| [`sdk-usage/`](./sdk-usage) | A tour of the Python SDK methods (`add`, `get`, `query`, `ingest_path`, `correct`, `contradictions`, `delete`) in local mode, with the remote-mode call shape shown too. | `uv run python examples/sdk-usage/main.py` |

## Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).
- The workspace packages resolve automatically through the root `uv` workspace,
  so `uv run` works from the repo root with no extra install step.
- The `mcp-agent` example's `main.py` does **not** need the optional `mcp`
  dependency; that is only required to serve a live MCP stdio server (see that
  example's README).

## Local-first guarantee

These examples demonstrate the Requirement 20 end-to-end flow
(`init → add → ingest → query → show → contradictions → mcp`) using only
on-device models and deterministic rules. Nothing here calls an external LLM API
or a cloud service.
