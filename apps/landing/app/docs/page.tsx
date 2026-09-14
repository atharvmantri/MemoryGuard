import Link from "next/link";

import { Callout, CodeBlock, DocsLayout, SiteFrame } from "../site";

const cards = [
  [
    "/docs/quickstart",
    "Quickstart",
    "Install MemoryGuard in one command and use the memoryguard command directly.",
  ],
  [
    "/docs/agent-capture",
    "Agent Capture",
    "Turn Codex, Cursor, Claude, or plain session files into reviewable memory candidates.",
  ],
  [
    "/docs/context-sync",
    "Context Sync",
    "Render approved project memory into AGENTS.md, CLAUDE.md, Copilot instructions, MEMORY.md, and Cursor rules.",
  ],
  [
    "/docs/security",
    "Security",
    "Local storage, deterministic redaction, and the alpha limitations you should know about.",
  ],
] as const;

export default function DocsPage() {
  return (
    <SiteFrame>
      <DocsLayout
        title="MemoryGuard docs"
        description="Practical notes for the public open-source alpha. MemoryGuard runs locally from source today and does not require a hosted service."
      >
        <Callout type="alpha" title="Open-source alpha">
          MemoryGuard is a public open-source alpha. There is no PyPI or npm
          package yet. The supported install path is a one-command bootstrap
          that clones the public repo, runs <code>uv sync</code>, and wires up
          a direct <code>memoryguard</code> command on your <code>PATH</code>.
          Hosted sync, team workspaces, billing, and SSO are not part of the alpha.
        </Callout>

        <h2>Install the <code>memoryguard</code> command</h2>
        <CodeBlock>{`# Windows (PowerShell)
irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.ps1 | iex

# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.sh | bash

memoryguard doctor
memoryguard demo`}</CodeBlock>
        <p>
          The full walk-through is on the <Link href="/docs/quickstart">Quickstart</Link>{" "}
          page. The one-liner clones the repo into a stable source directory,
          runs <code>uv sync</code>, writes a thin wrapper, and adds it to
          your <code>PATH</code>. After it finishes, daily commands are{" "}
          <code>memoryguard init</code>, <code>memoryguard remember ...</code>,{" "}
          <code>memoryguard sync</code>, and so on &mdash; no{" "}
          <code>uv run</code> in front of every call.
        </p>

        <div className="docs-grid">
          {cards.map(([href, title, body]) => (
            <Link className="doc-card" href={href} key={href}>
              <h3>{title}</h3>
              <p>{body}</p>
            </Link>
          ))}
        </div>

        <h2>CLI surface</h2>
        <p>The alpha CLI covers the daily local workflow:</p>
        <CodeBlock>{`memoryguard doctor
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard capture file ./session.txt --source codex
memoryguard capture pending
memoryguard capture approve --all
memoryguard sync
memoryguard status
memoryguard demo`}</CodeBlock>

        <h2>What MemoryGuard does today</h2>
        <p>
          MemoryGuard keeps the context files used by AI coding tools aligned
          with approved project memory. The alpha includes:
        </p>
        <ul>
          <li>A local SQLite memory store under <code>.memoryguard/</code>.</li>
          <li>Context Sync for the files coding tools already read.</li>
          <li>Obvious supersession handling (FastAPI &rarr; Flask, etc.).</li>
          <li>Best-effort deterministic secret redaction.</li>
          <li>
            A review-first Agent Capture workflow with pending approval and{" "}
            <code>memoryguard capture reject</code>.
          </li>
        </ul>

        <h2>How to think about MemoryGuard</h2>
        <p>
          MemoryGuard is not a general chatbot memory layer and not a full
          development-history system. It is a focused local tool for turning
          reviewed project decisions into maintained agent context files.
          It runs locally, has no required hosted model, and stores state in
          a SQLite file under your project.
        </p>
      </DocsLayout>
    </SiteFrame>
  );
}
