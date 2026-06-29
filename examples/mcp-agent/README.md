# MCP agent integration

Connect an AI coding agent to MemoryGuard over the **Model Context Protocol
(MCP)** so the agent can store and retrieve *trusted, in-scope* memories with
provenance during a task. The MCP server runs locally with **no external LLM
API**.

## Register the MCP server in your agent

MemoryGuard ships an MCP stdio server. Start it either via the console script
or the module:

```bash
memoryguard-mcp
# or
python -m memoryguard_mcp
```

The SQLite store location is taken from the `MEMORYGUARD_STORE` environment
variable (default `memoryguard.db`).

Most MCP-capable agents (Claude Desktop, Cursor, and similar) read an
`mcpServers` block in their MCP config. Add MemoryGuard like this:

```json
{
  "mcpServers": {
    "memoryguard": {
      "command": "memoryguard-mcp",
      "args": [],
      "env": {
        "MEMORYGUARD_STORE": "./memoryguard.db"
      }
    }
  }
}
```

If `memoryguard-mcp` is not on your `PATH`, use the module form instead:

```json
{
  "mcpServers": {
    "memoryguard": {
      "command": "python",
      "args": ["-m", "memoryguard_mcp"],
      "env": {
        "MEMORYGUARD_STORE": "./memoryguard.db"
      }
    }
  }
}
```

> The MCP transport needs the optional `mcp` dependency:
> `pip install "memoryguard-mcp-server[mcp]"` (or `pip install mcp`).
> It is **not** required to run `main.py` below.

## Tools the agent gets

- `memory_search` — relevant **and** trusted retrieval. Applies a default
  `min_trust` floor of **0.5** when none is supplied, and returns each result's
  `source_ref` + `trust_score` + reasons.
- `memory_add` — store a memory with provenance.
- `memory_explain` — trust rationale + provenance for a memory.

Plus the resources `memoryguard://project/{scope_ref}/memories` and
`memoryguard://memory/{memory_id}`.

## Run the demo script

`main.py` calls the MCP tool handler functions directly against a local engine —
no transport, no agent, no network — so you can see exactly what an agent would
receive:

```bash
uv run python examples/mcp-agent/main.py
```

It imports the handlers straight from the server module:

```python
from memoryguard_core import build_local_engine
from memoryguard_mcp.server import tool_memory_add, tool_memory_search

engine = build_local_engine(":memory:")
tool_memory_add(engine, content="...", source_ref="slack://eng/standup",
                scope="project", scope_ref="acme")
hits = tool_memory_search(engine, query="when is standup?",
                          scope="project", scope_ref="acme")
```

## Requirements covered

- **20.2 / 20.3** — an agent retrieves trusted, in-scope memories with
  provenance and trust score over MCP.
- **20.5** — the MCP step runs with no commercial module or cloud service.
