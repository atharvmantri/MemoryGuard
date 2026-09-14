import { Callout, DocsLayout, SiteFrame } from "../../site";

export default function SecurityPage() {
  return (
    <SiteFrame>
      <DocsLayout
        title="Security and local-first notes"
        description="MemoryGuard reduces accidental context leakage with local storage and deterministic redaction, but it is not magic."
      >
        <Callout type="warning" title="Public alpha warning">
          Expect rough edges. Keep backups of important context files and
          inspect generated output before sharing it outside your machine.
        </Callout>

        <h2>Local-first</h2>
        <p>
          The alpha workflow stores MemoryGuard data locally. No cloud service
          and no external LLM API is required for the core capture, memory,
          and Context Sync path described in these docs.
        </p>

        <h2>SQLite under <code>.memoryguard</code></h2>
        <p>
          The local store uses SQLite under <code>.memoryguard</code>. Treat
          that directory as project-local state and decide deliberately
          whether it belongs in version control for your workflow.
        </p>

        <h2>Best-effort deterministic redaction</h2>
        <p>
          MemoryGuard attempts to detect and omit secret-looking content
          before rendering generated context. This is a guardrail, not a
          guarantee. Review <code>AGENTS.md</code>, <code>CLAUDE.md</code>,{" "}
          <code>.github/copilot-instructions.md</code>, <code>MEMORY.md</code>, and
          the Cursor rules before publishing.
        </p>

        <h2>Transcript hygiene</h2>
        <p>
          Do not paste real secrets into transcripts when avoidable. If a
          session file contains credentials or sensitive customer information,
          clean it before capture or reject the sensitive candidate during
          review.
        </p>

        <h2>Trust and provenance</h2>
        <p>
          Every memory carries a source, a scope, a status, a sensitivity
          tier, and a trust score. <code>memoryguard show &lt;id&gt;</code>{" "}
          prints the full breakdown, and the Context Sync engine only renders
          memories that pass the trust, scope, and policy filters.
        </p>

        <h2>Open-core boundary</h2>
        <p>
          The OSS core is the part you are looking at. The open-core boundary
          is enforced in code: <code>memoryguard-core</code> and{" "}
          <code>memoryguard-models</code> never import from a commercial
          package, and any commercial behavior enters the core only through
          stable interfaces gated by feature flags that default off.
        </p>

        <Callout type="note" title="Best-effort guardrail">
          Secret detection helps reduce accidental leakage, but it does not
          replace normal secret handling discipline. If a secret must remain
          secret, do not paste it into a transcript or a memory at all.
        </Callout>
      </DocsLayout>
    </SiteFrame>
  );
}
