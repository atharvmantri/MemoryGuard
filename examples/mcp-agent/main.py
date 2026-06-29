"""MemoryGuard MCP tool example (local-first, no external LLM API).

An AI coding agent normally reaches MemoryGuard over the Model Context Protocol
(see README.md in this folder for the agent config). Under the hood, every MCP
tool is a plain handler function that takes an explicit ``engine`` argument, so
this script can call those exact handlers directly — no MCP transport, no agent,
no network — to demonstrate the trusted-retrieval behaviour an agent would get.

It:

1. builds a local engine (the same one ``memoryguard-mcp`` serves),
2. calls ``tool_memory_add`` to store memories with provenance, and
3. calls ``tool_memory_search`` to retrieve only trusted, in-scope memories,
   showing the default 0.5 trust floor the MCP server applies.

Run it with::

    uv run python examples/mcp-agent/main.py

This example is Apache-2.0 OSS and depends only on the ``memoryguard`` packages
and the Python standard library. The optional ``mcp`` dependency is NOT required
to run this script (it is only needed to serve a live MCP stdio server).
"""

from __future__ import annotations

from memoryguard_core import build_local_engine
from memoryguard_mcp.server import (
    DEFAULT_MIN_TRUST,
    tool_memory_add,
    tool_memory_explain,
    tool_memory_search,
)


def _rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> int:
    _rule("MemoryGuard MCP tool example (local-first, no external LLM API)")

    # The MCP server wraps exactly this engine; we build it directly here.
    engine = build_local_engine(":memory:")
    print(f"default MCP trust floor: {DEFAULT_MIN_TRUST}")

    # --- 1. memory_add — store memories with provenance. ---------------------
    _rule("1. memory_add — store memories the agent learned")

    added = tool_memory_add(
        engine,
        content="The team standup is every weekday at 9:30am in the #eng channel.",
        source_ref="slack://eng/standup",
        scope="project",
        scope_ref="acme",
    )
    print(f"added {added['memory_id']}  trust={added['trust_score']:.2f}  "
          f"source={added['source_ref']}")

    tool_memory_add(
        engine,
        content="The service is deployed to AWS region us-east-1.",
        source_ref="repo://infra/deploy.md",
        scope="project",
        scope_ref="acme",
    )

    # --- 2. memory_search — only trusted, in-scope memories come back. -------
    _rule("2. memory_search — trusted retrieval (default 0.5 floor applied)")

    search = tool_memory_search(
        engine,
        query="when is the daily standup?",
        scope="project",
        scope_ref="acme",
        # min_trust omitted on purpose: the MCP server applies its 0.5 default.
        limit=5,
    )
    print(f"effective min_trust: {search['min_trust']}   results: {search['count']}")
    for rank, result in enumerate(search["results"], start=1):
        print(f"\n#{rank}  trust={result['trust_score']:.2f}")
        print(f"    content: {result['content']}")
        print(f"    source:  {result['source_ref']}  ({result['scope']}/{result['scope_ref']})")
        print(f"    reason:  {result['reasons'][0] if result['reasons'] else '-'}")

    # --- 3. memory_explain — provenance + trust rationale for a result. ------
    if search["results"]:
        first_id = search["results"][0]["memory_id"]
        _rule("3. memory_explain — provenance + trust rationale")
        explanation = tool_memory_explain(engine, first_id)
        print(f"  memory_id:   {explanation['memory_id']}")
        print(f"  trust_score: {explanation['trust_score']:.3f}")
        prov = explanation["provenance"]
        print(f"  provenance:  {prov['source_type']} {prov['source_ref']}")
        print("  trust signals:")
        for name, value in explanation["signals"].items():
            print(f"      {name:<22} {float(value):.3f}")

    _rule("Done — MCP tool handlers exercised locally with no external LLM API.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
