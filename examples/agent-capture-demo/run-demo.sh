#!/usr/bin/env bash
set -euo pipefail

demo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
transcript="$demo_dir/transcript.txt"
project="$(mktemp -d "${TMPDIR:-/tmp}/memoryguard-agent-capture-demo.XXXXXX")"

cleanup() {
  rm -rf "$project"
}
trap cleanup EXIT

cd "$project"
memoryguard init --name agent-capture-demo
memoryguard remember "This project uses FastAPI for the backend."
memoryguard remember "This project uses npm."
memoryguard remember "This project uses MySQL as the database."
memoryguard capture file "$transcript" --source codex
memoryguard capture pending
memoryguard capture approve --all
memoryguard sync

cat AGENTS.md
all_context="$(cat AGENTS.md CLAUDE.md MEMORY.md .cursor/rules/memoryguard.mdc)"

grep -q "Backend framework: Flask" AGENTS.md
grep -q "FastAPI" AGENTS.md
grep -q "Package manager: pnpm" AGENTS.md
grep -q "npm" AGENTS.md
grep -q "Local database: SQLite" AGENTS.md
grep -q "Cloud database: PostgreSQL" AGENTS.md
grep -q "Deployment target: Vercel" AGENTS.md
grep -q "Test command: pnpm test" AGENTS.md
if printf "%s" "$all_context" | grep -q "sk-test-1234567890abcdef"; then
  echo "Fake secret leaked" >&2
  exit 1
fi
test -f AGENTS.md
test -f CLAUDE.md
test -f MEMORY.md
test -f .cursor/rules/memoryguard.mdc

echo "Agent Capture demo passed."
