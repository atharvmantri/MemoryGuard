"""MemoryGuard repo / folder ingestion example (local-first, no external LLM API).

Creates a small sample documentation folder in a temporary directory, ingests it
into a local MemoryGuard store (one memory per content chunk, each carrying
file provenance), and then runs a trust-aware query over the ingested memories.

This mirrors what the CLI does with::

    memoryguard init
    memoryguard ingest ./docs --scope project --scope-ref my-app
    memoryguard query "how do I configure logging?" --scope project --scope-ref my-app

Run it with::

    uv run python examples/repo-ingest/main.py

Point it at a real folder instead of the generated sample::

    uv run python examples/repo-ingest/main.py --path ./docs

Everything runs on-device; the only model used is the local embedder.
This example is Apache-2.0 OSS and depends only on the ``memoryguard`` packages
and the Python standard library.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from memoryguard import MemoryGuard, Scope

SCOPE_REF = "sample-app"

#: A tiny doc set written to a temp folder when no --path is supplied.
SAMPLE_DOCS: dict[str, str] = {
    "logging.md": (
        "# Logging\n\n"
        "MemoryGuard never writes secret values to logs. Configure the log "
        "level with the MEMORYGUARD_LOG_LEVEL environment variable. The default "
        "level is INFO. Use DEBUG only in local development.\n"
    ),
    "storage.md": (
        "# Storage\n\n"
        "In local mode all data is stored on-device in a single SQLite file. "
        "The schema is created by migration 0001 and defines the memories, "
        "memory_contradictions, memory_embeddings, and memory_fts tables.\n"
    ),
    "retrieval.md": (
        "# Retrieval\n\n"
        "Retrieval combines semantic similarity, keyword matching, and recency, "
        "then filters by trust, scope, and sensitivity. Every returned memory "
        "carries at least one human-readable reason explaining why it surfaced.\n"
    ),
}


def _write_sample_docs(root: Path) -> None:
    """Write the SAMPLE_DOCS into ``root`` (created if missing)."""

    root.mkdir(parents=True, exist_ok=True)
    for name, body in SAMPLE_DOCS.items():
        (root / name).write_text(body, encoding="utf-8")


def _rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def run(target: Path) -> int:
    _rule("MemoryGuard repo-ingest example (local-first, no external LLM API)")
    print(f"ingesting: {target}")

    # An in-memory store keeps this example side-effect free.
    with MemoryGuard.local(":memory:") as mg:
        # --- Ingest the folder. One memory is created per content chunk. -----
        created = mg.ingest_path(
            str(target),
            scope=Scope.PROJECT,
            scope_ref=SCOPE_REF,
        )
        _rule(f"Ingested {len(created)} memory chunk(s)")
        for memory in created:
            preview = " ".join(memory.content.split())[:64]
            print(f"  {memory.memory_id}  trust={memory.trust_score:.2f}  "
                  f"src={memory.source_ref}")
            print(f"      {preview}…")

        # --- Query the freshly ingested memories. ----------------------------
        question = "how do I configure logging?"
        _rule(f"Trust-aware query: {question!r}")
        results = mg.query(
            text=question,
            scope=Scope.PROJECT,
            scope_ref=SCOPE_REF,
            min_trust=0.0,
            limit=3,
        )
        if not results:
            print("(no results)")
        for rank, result in enumerate(results, start=1):
            memory = result.memory
            preview = " ".join(memory.content.split())[:80]
            print(f"\n#{rank}  trust={memory.trust_score:.2f}  rank={result.final_rank:.3f}")
            print(f"    source:  {memory.source_ref}")
            print(f"    content: {preview}…")
            print(f"    reason:  {result.reasons[0] if result.reasons else '-'}")

    _rule("Done — folder ingested and queried locally, no external LLM API.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MemoryGuard repo-ingest example")
    parser.add_argument(
        "--path",
        default=None,
        help="Folder to ingest. Defaults to a generated temporary sample folder.",
    )
    args = parser.parse_args()

    if args.path:
        return run(Path(args.path).resolve())

    # No path supplied: generate a self-contained sample folder in a temp dir.
    with tempfile.TemporaryDirectory(prefix="memoryguard-sample-docs-") as tmp:
        sample_root = Path(tmp) / "docs"
        _write_sample_docs(sample_root)
        return run(sample_root)


if __name__ == "__main__":
    raise SystemExit(main())
