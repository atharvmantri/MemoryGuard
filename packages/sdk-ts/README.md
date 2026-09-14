# `@memoryguard/sdk`

The open-source TypeScript client for a MemoryGuard REST API. It keeps the
camelCase TypeScript surface aligned with the API's snake_case JSON wire format
and uses the platform `fetch` implementation, so it has no SDK-specific
network dependency.

## Build and test from this repository

```bash
pnpm install --frozen-lockfile
pnpm --filter @memoryguard/sdk build
pnpm --filter @memoryguard/sdk test
```

The package currently ships as part of the alpha monorepo. Its compiled output
is written to `dist/` and is excluded from source control.

## Remote usage

```ts
import {
  MemoryGuard,
  Scope,
  Sensitivity,
  SourceType,
} from "@memoryguard/sdk";

const memoryguard = MemoryGuard.remote({
  baseUrl: "http://127.0.0.1:8000",
  token: process.env.MEMORYGUARD_TOKEN,
});

const memory = await memoryguard.add({
  content: "billing-svc uses PostgreSQL 15",
  sourceType: SourceType.File,
  sourceRef: "repo://billing-svc/README.md",
  scope: Scope.Repo,
  scopeRef: "billing-svc",
  sensitivity: Sensitivity.Internal,
});

const results = await memoryguard.query({
  text: "Which database does billing-svc use?",
  scope: Scope.Repo,
  scopeRef: "billing-svc",
  minTrust: 0.5,
  limit: 5,
});

console.log(memory.memoryId, results[0]?.memory.content);
```

`baseUrl` may include a path prefix but does not need a trailing slash. When a
token is supplied, requests send `Authorization: Bearer <token>`. Inject a
custom `fetch` function through `RemoteOptions.fetch` for tests or a runtime
with its own transport.

## Supported operations

| Method | REST route | Result |
| --- | --- | --- |
| `add` | `POST /v1/memories` | Created `Memory` |
| `get` | `GET /v1/memories/{memory_id}` | One `Memory` |
| `query` | `POST /v1/query` | Ranked `RetrievedMemory[]` |
| `ingestPath` | `POST /v1/ingest/path` | Created count and IDs |
| `correct` | `PATCH /v1/memories/{memory_id}` | Corrected `Memory` |
| `delete` | `DELETE /v1/memories/{memory_id}` | `void` |
| `contradictions` | `GET /v1/memories/{memory_id}/contradictions` | `Contradiction[]` |

Non-2xx responses throw `MemoryGuardError`, which includes the HTTP `status`
and parsed response `body` for structured handling.

