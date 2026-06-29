# Python SDK usage

A tour of the common `MemoryGuard` Python SDK methods. The script runs in
**local** mode (on-device, **no external LLM API**) and shows the identical
**remote** call shape for a hosted REST API.

## What it does

`main.py` exercises the full SDK surface against a local store:

- `add` — create a memory with provenance, scope, sensitivity, and tags.
- `get` — fetch a memory by id.
- `ingest_path` — ingest a file/folder into chunked memories.
- `query` — trust-aware retrieval with per-result reasons.
- `correct` — record a corrected lineage (old record becomes `corrected`).
- `contradictions` — list conflicts linked to a memory.
- `delete` — soft-delete (the record is retained for audit but excluded from
  queries).

## Run it

```bash
uv run python examples/sdk-usage/main.py
```

## Local vs remote

Both constructors expose the same methods and return the same result shapes
(content, `trust_score`, `source_ref`, reasons):

```python
from memoryguard import MemoryGuard, Scope, SourceType

# Local — runs directly against the in-process core engine.
mg = MemoryGuard.local(":memory:")          # or a path like "./memoryguard.db"

# Remote — talks to the REST API with an optional bearer token.
mg = MemoryGuard.remote("http://127.0.0.1:8000", token="YOUR_TOKEN")
```

To try remote mode, start the API first:

```bash
uvicorn memoryguard_api.main:app --host 127.0.0.1 --port 8000
```

## Requirements covered

- **20.2** — create, ingest, query, correct, and inspect memories with
  provenance and trust.
- **20.5** — all local-mode operations run with no commercial module or cloud
  service.
