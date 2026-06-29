"""MemoryGuard quickstart — the Phase 1 developer flow in one script.

Runs entirely on-device with **no external LLM API**. It uses the OSS Python
SDK (``from memoryguard import MemoryGuard``) to:

1. build a local store (a file ``./quickstart.db`` by default, or ``:memory:``),
2. add a couple of memories — one that *contradicts* another,
3. run a trust-aware query and print each result's provenance + trust + reasons,
4. show the detected contradiction, and
5. explain a memory (provenance + trust-signal breakdown).

Run it with::

    uv run python examples/quickstart/main.py

or, for an ephemeral in-memory store that leaves no file behind::

    uv run python examples/quickstart/main.py --memory

This example is Apache-2.0 OSS and depends only on the ``memoryguard`` packages
and the Python standard library.
"""

from __future__ import annotations

import sys
from pathlib import Path

from memoryguard import MemoryGuard, Scope, Sensitivity, SourceType


def _rule(title: str) -> None:
    """Print a simple section header."""

    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Choose the store location: a file next to this script, or :memory:.
    if "--memory" in argv:
        store_path = ":memory:"
    else:
        store_path = str(Path(__file__).resolve().parent / "quickstart.db")

    _rule("MemoryGuard quickstart (local-first, no external LLM API)")
    print(f"store: {store_path}")

    # MemoryGuard.local(...) talks directly to the in-process core engine.
    with MemoryGuard.local(store_path) as mg:
        print(f"mode:  {mg.mode}")

        # --- 1. Add a couple of memories. The two API-limit facts conflict. ---
        _rule("1. Adding memories (one contradicts another)")

        confirmed = mg.add(
            content="The production API rate limit is 100 requests per minute.",
            source_type=SourceType.FILE,
            source_ref="repo://docs/api.md@v1",
            scope=Scope.PROJECT,
            scope_ref="demo",
            sensitivity=Sensitivity.INTERNAL,
        )
        print(f"added  {confirmed.memory_id}  trust={confirmed.trust_score:.2f}")
        print(f"       {confirmed.content!r}")

        conflicting = mg.add(
            content="The production API rate limit is 500 requests per minute.",
            source_type=SourceType.USER,
            source_ref="user://stale-note",
            scope=Scope.PROJECT,
            scope_ref="demo",
            sensitivity=Sensitivity.INTERNAL,
        )
        print(f"added  {conflicting.memory_id}  trust={conflicting.trust_score:.2f}")
        print(f"       {conflicting.content!r}")

        # An unrelated, in-scope memory so the query has more to choose from.
        mg.add(
            content="Deployments are gated behind a green CI run on main.",
            source_type=SourceType.FILE,
            source_ref="repo://docs/deploy.md@v1",
            scope=Scope.PROJECT,
            scope_ref="demo",
        )

        # --- 2. Run a trust-aware query and print provenance + trust + reasons.
        _rule("2. Trust-aware query: 'what is the API rate limit?'")

        results = mg.query(
            text="what is the API rate limit?",
            scope=Scope.PROJECT,
            scope_ref="demo",
            min_trust=0.0,  # show everything; raise this to enforce a trust floor
            limit=5,
        )
        if not results:
            print("(no results)")
        for rank, result in enumerate(results, start=1):
            memory = result.memory
            print(f"\n#{rank}  trust={memory.trust_score:.2f}  rank={result.final_rank:.3f}")
            print(f"    content:    {memory.content}")
            print(f"    provenance: {memory.source_type} {memory.source_ref}")
            print(f"    scope:      {memory.scope}/{memory.scope_ref}")
            print("    reasons:")
            for reason in result.reasons:
                print(f"      - {reason}")

        # --- 3. Show the detected contradiction. -----------------------------
        _rule("3. Contradictions detected for the confirmed fact")

        conflicts = mg.contradictions(confirmed.memory_id)
        if not conflicts:
            print(
                "(no contradiction detected — the two facts were not similar enough\n"
                " for the local heuristic detector to link them)"
            )
        for conflict in conflicts:
            print(f"  conflicts with: {conflict.memory_id}")
            print(f"    source:     {conflict.source_ref}")
            print(f"    status:     {conflict.status}")
            print(f"    confidence: {conflict.confidence:.2f}")
            print(f"    reason:     {conflict.reason}")

        # --- 4. Explain a memory: provenance + trust-signal breakdown. -------
        _rule("4. Explain the confirmed memory (provenance + trust signals)")

        # The local backend exposes the underlying engine, whose `explain`
        # returns the full provenance + per-signal trust breakdown.
        explanation = mg.backend.engine.explain(confirmed.memory_id)
        print(f"  memory_id:   {explanation['memory_id']}")
        print(f"  status:      {explanation['status']}")
        print(f"  trust_score: {explanation['trust_score']:.3f}")
        prov = explanation["provenance"]
        print(f"  provenance:  {prov['source_type']} {prov['source_ref']} "
              f"({prov['scope']}/{prov['scope_ref']})")
        print("  trust signals:")
        for name, value in explanation["signals"].items():
            print(f"      {name:<22} {float(value):.3f}")
        print("  weights:")
        for name, value in explanation["weights"].items():
            print(f"      {name:<22} {float(value):.3f}")

    _rule("Done — everything above ran locally with no external LLM API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
