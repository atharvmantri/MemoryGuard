import { Callout, CodeBlock, DocsLayout, SiteFrame } from "../../site";

export default function AgentCapturePage() {
  return (
    <SiteFrame>
      <DocsLayout
        title="Agent Capture"
        description="Turn local session files into reviewable memory candidates before they become durable project context."
      >
        <Callout type="note" title="Review-first by design">
          Capture proposes memories. Approval is explicit. A transcript never
          becomes permanent project truth just because it was captured.
        </Callout>

        <h2>Workflow</h2>
        <ol>
          <li>
            <strong>Capture</strong> a transcript or local session file.
          </li>
          <li>
            <strong>Review</strong> the pending candidates.
          </li>
          <li>
            <strong>Approve</strong> only the facts that should survive future
            sessions.
          </li>
          <li>
            <strong>Sync</strong> the generated context files.
          </li>
        </ol>
        <CodeBlock>{`memoryguard capture file ./codex-session.txt --source codex
memoryguard capture pending
memoryguard capture approve --all
memoryguard sync`}</CodeBlock>

        <h2>What it is for</h2>
        <p>
          Coding sessions contain decisions that are easy to lose: changed
          package managers, renamed modules, replaced frameworks, deployment
          choices, test commands, and project conventions. Agent Capture
          extracts those candidates without trusting the whole transcript.
        </p>
        <p>
          Supported sources: <code>codex</code>, <code>claude-code</code>,{" "}
          <code>cursor</code>, and plain <code>text</code> files. See{" "}
          <code>memoryguard capture file --help</code> for the full list.
        </p>

        <h2>What to review</h2>
        <p>
          Approve facts that are durable, current, and safe to place in context
          files. Reject:
        </p>
        <ul>
          <li>Real secrets or credentials pasted into a session.</li>
          <li>One-off debugging notes.</li>
          <li>Speculative ideas and half-formed plans.</li>
          <li>Facts likely to change within the same session.</li>
        </ul>

        <h2>Rejecting a candidate</h2>
        <CodeBlock>{`memoryguard capture reject <candidate_id>

# once you are done, clear the rejected queue
memoryguard capture clear-rejected`}</CodeBlock>

        <h2>Transcript hygiene</h2>
        <p>
          Avoid putting real secrets into transcripts. If a session file
          contains credentials or sensitive customer information, clean it
          first or reject the sensitive candidate during review. Secret
          redaction in the extractor is best-effort, not a guarantee.
        </p>

        <Callout type="note" title="No external LLM API required">
          The first Agent Capture extractor is deterministic and rules-first. It
          does not call OpenAI, Anthropic, Gemini, or any hosted model.
          Capture and approval run entirely on your machine.
        </Callout>
      </DocsLayout>
    </SiteFrame>
  );
}
