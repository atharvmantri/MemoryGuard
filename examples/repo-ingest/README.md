# Repo / folder ingestion

Ingest a folder of documents into MemoryGuard and query it back — all on-device,
with **no external LLM API**.

## What it does

`main.py`:

1. Writes a small sample documentation folder to a temporary directory (or uses
   a folder you point it at).
2. Ingests the folder with the SDK's `ingest_path(...)` — one memory record per
   content chunk, each carrying **file provenance** (`source_ref`) and the given
   scope.
3. Runs a **trust-aware query** over the ingested memories and prints the top
   matches with their source and reasons.

## Run it

```bash
uv run python examples/repo-ingest/main.py
```

Point it at a real folder (for example the repo's own `docs/`):

```bash
uv run python examples/repo-ingest/main.py --path ./docs
```

## Equivalent CLI: `memoryguard ingest`

The script mirrors what the CLI does:

```bash
# 1. create a local store
memoryguard init

# 2. ingest a file, folder, or git repository
memoryguard ingest ./docs --scope project --scope-ref my-app

# 3. query the ingested memories
memoryguard query "how do I configure logging?" \
  --scope project --scope-ref my-app
```

`memoryguard ingest` accepts a file, a folder, or a git repository. For a git
repo it also attaches the repository and (where available) the commit reference
to each memory's provenance.

## Requirements covered

- **20.2** — ingest documents/a repository and run a trust-aware query that
  surfaces provenance and trust.
- **20.5** — ingestion and retrieval run with no commercial module or cloud
  service.
