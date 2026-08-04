import { Callout, CodeBlock, DocsLayout, SiteFrame } from "../../site";

export default function ContextSyncPage() {
  return (
    <SiteFrame>
      <DocsLayout
        title="Context Sync"
        description="Render approved project memory into the files that coding agents and editors already inspect."
      >
        <h2>What it writes</h2>
        <p>Context Sync writes managed blocks to:</p>
        <ul>
          <li>
            <code>AGENTS.md</code>
          </li>
          <li>
            <code>CLAUDE.md</code>
          </li>
          <li>
            <code>MEMORY.md</code>
          </li>
          <li>
            <code>.cursor/rules/memoryguard.mdc</code>
          </li>
        </ul>
        <p>
          These are the same files your coding tools already read. MemoryGuard
          does not introduce a new file format; it keeps the familiar ones
          aligned with approved project memory.
        </p>

        <h2>Basic flow</h2>
        <CodeBlock>{`memoryguard remember "Package manager: pnpm."
memoryguard remember "FastAPI was superseded by Flask."
memoryguard sync
memoryguard status`}</CodeBlock>
        <p>
          <code>memoryguard sync</code> generates a context diff, writes the
          pending plan, and applies it in one step.{" "}
          <code>memoryguard context generate</code> shows the diff and waits for
          <code>memoryguard context approve</code> if you want a two-step flow.
        </p>

        <h2>Watch for drift</h2>
        <p>
          <code>memoryguard context watch</code> polls the project and either
          applies the diff (<code>--yes</code>) or prints a pending diff for
          you to approve.
        </p>
        <CodeBlock>{`# print a pending diff whenever memories change
memoryguard context watch

# apply immediately when changes are detected
memoryguard context watch --yes --once`}</CodeBlock>

        <h2>Supersession</h2>
        <p>
          Projects change. MemoryGuard is designed to preserve that change
          instead of hiding it. When a newer approved memory replaces an older
          one, generated context can show the current fact while keeping
          useful history such as &ldquo;FastAPI was previously used; superseded
          by Flask.&rdquo;
        </p>
        <p>
          Mark a superseded relationship explicitly with{" "}
          <code>memoryguard resolve</code>:
        </p>
        <CodeBlock>{`memoryguard resolve <old_memory_id> --superseded-by <new_memory_id>`}</CodeBlock>

        <h2>Managed blocks</h2>
        <p>
          MemoryGuard writes managed context blocks bounded by{" "}
          <code>&lt;!-- memoryguard:context:start --&gt;</code> and{" "}
          <code>&lt;!-- memoryguard:context:end --&gt;</code> markers. Rerun{" "}
          <code>memoryguard sync</code> whenever the truth of your project
          changes, and the managed section refreshes in place.
        </p>

        <h2>Review before publishing</h2>
        <p>
          Treat generated context as code-adjacent output. Review{" "}
          <code>AGENTS.md</code>, <code>CLAUDE.md</code>,{" "}
          <code>MEMORY.md</code>, and the Cursor rules before committing or
          sharing them.
        </p>
        <Callout type="warning" title="Best-effort redaction">
          Sensitive value detection is best-effort and deterministic. Always
          inspect generated context files before committing them or sharing
          them outside your machine.
        </Callout>
      </DocsLayout>
    </SiteFrame>
  );
}
