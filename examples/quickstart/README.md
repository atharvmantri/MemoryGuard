# Quickstart

The fastest tour of MemoryGuard's Phase 1 developer flow, using the Python SDK
(`from memoryguard import MemoryGuard`). Everything runs locally with **no
external LLM API** — the only model involved is the on-device embedder.

## What it does

`main.py` walks through the core loop end to end:

1. Builds a **local store** (a file `./quickstart.db`, or `:memory:`).
2. Adds a couple of memories — including two API rate-limit facts that
   **contradict** each other.
3. Runs a **trust-aware query** and prints each result with its provenance,
   trust score, and human-readable reasons.
4. Shows the **detected contradiction** between the conflicting facts.
5. **Explains** a memory: its provenance plus the full trust-signal breakdown.

## Run it

```bash
uv run python examples/quickstart/main.py
```

This writes a small SQLite store at `examples/quickstart/quickstart.db`. To run
against an ephemeral in-memory store that leaves no file behind:

```bash
uv run python examples/quickstart/main.py --memory
```

## Equivalent CLI flow

The same steps are available from the `memoryguard` CLI:

```bash
memoryguard init
memoryguard add "The production API rate limit is 100 requests per minute." \
  --source-type file --source-ref "repo://docs/api.md@v1" \
  --scope project --scope-ref demo
memoryguard query "what is the API rate limit?" --scope project --scope-ref demo
memoryguard show <memory-id>
memoryguard contradictions <memory-id>
```

## Requirements covered

- **20.2** — create a store, add memories, query with provenance + trust, detect
  contradictions.
- **20.5** — each step runs without any commercial module or cloud service.
