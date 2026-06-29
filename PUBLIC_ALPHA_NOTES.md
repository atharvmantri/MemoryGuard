# Public Alpha Notes

MemoryGuard public alpha is early software.

- Local-first by default: project state is stored under `.memoryguard/` in your repo.
- No external LLM API is required for the core workflow.
- Do not trust generated context blindly.
- Review pending capture candidates before approval.
- Secret redaction is best-effort and deterministic; inspect generated files before sharing.
- Please report bugs and sharp edges through GitHub issues.
- Hosted cloud is future work and is not included in this OSS alpha.
- There is no PyPI or npm package to install yet. The supported alpha path is
  a clone plus the one-time `scripts/install-alpha.{ps1,sh}` installer, which
  wires up a direct `memoryguard` command on your `PATH`. See `README.md` or
  `docs/getting-started.md` for the full flow.
