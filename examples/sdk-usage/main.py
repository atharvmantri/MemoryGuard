"""MemoryGuard Python SDK usage example (local-first, no external LLM API).

Walks through the common ``MemoryGuard`` methods against a local store:
``add``, ``get``, ``query``, ``ingest_path``, ``correct``, ``contradictions``,
and ``delete``. The same ``MemoryGuard`` surface also works against a remote
REST API via ``MemoryGuard.remote(url)`` — that path is shown (commented out) at
the bottom so this script needs no running server.

Run it with::

    uv run python examples/sdk-usage/main.py

This example is Apache-2.0 OSS and depends only on the ``memoryguard`` packages
and the Python standard library.
"""

from __future__ import annotations

from memoryguard import MemoryGuard, Scope, Sensitivity, SourceType


def _rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> int:
    _rule("MemoryGuard Python SDK usage (local-first, no external LLM API)")

    # MemoryGuard.local(path) runs directly against the in-process core engine.
    # Use ":memory:" for an ephemeral store, or a filesystem path to persist.
    with MemoryGuard.local(":memory:") as mg:
        print(f"mode: {mg.mode}")

        # --- add -------------------------------------------------------------
        _rule("add() — create a memory with provenance + scope")
        memory = mg.add(
            content="We use Postgres 16 in production.",
            source_type=SourceType.FILE,
            source_ref="repo://docs/stack.md",
            scope=Scope.PROJECT,
            scope_ref="acme",
            sensitivity=Sensitivity.INTERNAL,
            tags=["infra", "database"],
        )
        print(f"id={memory.memory_id}  trust={memory.trust_score:.2f}  tags={memory.tags}")

        # --- get -------------------------------------------------------------
        _rule("get() — fetch a memory by id")
        fetched = mg.get(memory.memory_id)
        assert fetched is not None
        print(f"content: {fetched.content}")
        print(f"source:  {fetched.source_type} {fetched.source_ref}")
        print(f"status:  {fetched.status}")

        # --- ingest_path -----------------------------------------------------
        _rule("ingest_path() — ingest this example file as memories")
        ingested = mg.ingest_path(
            __file__,
            scope=Scope.PROJECT,
            scope_ref="acme",
        )
        print(f"ingested {len(ingested)} chunk(s) from this script")

        # --- query -----------------------------------------------------------
        _rule("query() — trust-aware retrieval with reasons")
        results = mg.query(
            text="which database do we run in production?",
            scope=Scope.PROJECT,
            scope_ref="acme",
            min_trust=0.0,
            limit=3,
        )
        for rank, result in enumerate(results, start=1):
            preview = " ".join(result.memory.content.split())[:70]
            print(f"#{rank}  trust={result.memory.trust_score:.2f}  {preview}…")
            print(f"     reason: {result.reasons[0] if result.reasons else '-'}")

        # --- correct ---------------------------------------------------------
        _rule("correct() — record a corrected lineage (old -> corrected)")
        corrected = mg.correct(memory.memory_id, "We use Postgres 17 in production.")
        old = mg.get(memory.memory_id)
        print(f"old {memory.memory_id} status={old.status if old else '?'}")
        print(f"new {corrected.memory_id} content={corrected.content!r}")

        # --- contradictions --------------------------------------------------
        _rule("contradictions() — list conflicts linked to a memory")
        conflict = mg.add(
            content="We use Postgres 17 in production.",
            source_type=SourceType.USER,
            source_ref="user://note",
            scope=Scope.PROJECT,
            scope_ref="acme",
        )
        another = mg.add(
            content="We use MySQL 8 in production.",
            source_type=SourceType.USER,
            source_ref="user://other-note",
            scope=Scope.PROJECT,
            scope_ref="acme",
        )
        conflicts = mg.contradictions(conflict.memory_id)
        if conflicts:
            for c in conflicts:
                print(f"  {conflict.memory_id} conflicts with {c.memory_id} "
                      f"(confidence {c.confidence:.2f}): {c.reason}")
        else:
            print("  (no contradiction linked to this memory)")
        _ = another  # added only to give the detector a conflicting neighbour

        # --- delete ----------------------------------------------------------
        _rule("delete() — soft-delete (retained, but excluded from queries)")
        mg.delete(conflict.memory_id)
        after = mg.get(conflict.memory_id)
        print(f"  {conflict.memory_id} status after delete: "
              f"{after.status if after else 'gone'} (still retrievable for audit)")

    # --- remote mode (optional) ---------------------------------------------
    _rule("Remote mode (optional — requires a running REST API)")
    print(
        "The identical surface works against a hosted/remote API:\n\n"
        "    from memoryguard import MemoryGuard, Scope, SourceType\n\n"
        "    mg = MemoryGuard.remote('http://127.0.0.1:8000', token='YOUR_TOKEN')\n"
        "    mg.add(\n"
        "        content='We use Postgres 16 in production.',\n"
        "        source_type=SourceType.FILE,\n"
        "        source_ref='repo://docs/stack.md',\n"
        "        scope=Scope.PROJECT,\n"
        "        scope_ref='acme',\n"
        "    )\n"
        "    results = mg.query('which database?', scope=Scope.PROJECT, scope_ref='acme')\n"
        "    mg.close()\n\n"
        "Start the API first with:\n"
        "    uvicorn memoryguard_api.main:app --host 127.0.0.1 --port 8000\n"
    )

    _rule("Done — SDK methods exercised locally with no external LLM API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
