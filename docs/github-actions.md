# GitHub Actions integration

MemoryGuard includes a small composite action for checking a project from CI.
It installs the alpha directly from this repository, runs locally on the
runner, and does not require a cloud account or an external LLM API.

## Quick start

Create `.github/workflows/memoryguard.yml` in the project you want to inspect:

```yaml
name: MemoryGuard

on:
  pull_request:

permissions:
  contents: read

jobs:
  doctor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: atharvmantri/MemoryGuard@main
        with:
          project-path: .
```

The default mode is lenient: it fails on real installation/runtime errors but
allows a project that has not created a `.memoryguard` store yet. This makes the
first run useful as an installation check.

Once the project has an initialized store and you want warnings to fail CI,
enable strict mode:

```yaml
- uses: atharvmantri/MemoryGuard@main
  with:
    project-path: .
    strict: true
```

For production workflows, pin `uses` to a reviewed commit instead of `main`.
The action uses `uv` to run the source in the action checkout; it does not
publish or install a package from PyPI.

## What the action does

- checks that the CLI and its local dependencies can start;
- inspects the selected project path;
- reports the current store/context-file state through `memoryguard doctor`;
- does not write project files or approve generated context.

The action is intentionally read-only. Use the local CLI's explicit
`memoryguard context update --yes` command when you want to write generated
context files as a separate, reviewed step.
