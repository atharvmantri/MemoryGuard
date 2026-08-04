import {
  Badge,
  BentoCard,
  Button,
  SiteFrame,
  Terminal,
  githubUrl,
} from "./site";

/* ── Data ────────────────────────────────────────────────── */

const statusPills = [
  "Local-first",
  "No required LLM API",
  "Review-first capture",
  "Apache-2.0",
];

const trustItems = [
  "Public OSS alpha",
  "CI passing",
  "Apache-2.0",
  "Runs locally",
  "No required cloud",
  "No required external LLM API",
];

const features: {
  kicker: string;
  title: string;
  body: string;
  icon: string;
  wide?: boolean;
}[] = [
  {
    kicker: "Sync",
    title: "One memory store. Many agent files.",
    body: "Render approved project memory into AGENTS.md, CLAUDE.md, MEMORY.md, and Cursor rules.",
    icon: "⇄",
    wide: true,
  },
  {
    kicker: "Capture",
    title: "Turn sessions into reviewable memory.",
    body: "Capture durable facts from Codex, Cursor, Claude, or plain local session files without trusting the whole transcript.",
    icon: "◎",
  },
  {
    kicker: "Current",
    title: "Old decisions stay old.",
    body: "When FastAPI becomes Flask or npm becomes pnpm, MemoryGuard marks the replaced facts as superseded instead of letting them linger.",
    icon: "↻",
    wide: true,
  },
  {
    kicker: "Redact",
    title: "Keep sensitive values out of context.",
    body: "Secret-looking values are omitted from generated context as a best-effort guardrail.",
    icon: "◆",
  },
  {
    kicker: "SQLite",
    title: "Runs on your machine.",
    body: "Project state lives locally under .memoryguard using SQLite in the alpha flow.",
    icon: "⬡",
  },
  {
    kicker: "Offline",
    title: "No hosted model dependency.",
    body: "The core workflow runs without OpenAI, Anthropic, Gemini, or any required external model API.",
    icon: "⊘",
  },
];

const whyItems = [
  "Capture decisions from local session files",
  "Review candidates before they become memory",
  "Supersede outdated project facts",
  "Generate the files coding tools already read",
  "Keep multiple agent files aligned",
  "Omit secret-looking values from generated context",
  "Run locally without a required hosted model",
  "Start from source during public alpha",
];

type CellKind = "yes" | "partial" | "no";

const compRows: {
  capability: string;
  manual: CellKind;
  generic: CellKind;
  mg: CellKind;
}[] = [
  { capability: "Syncs AGENTS.md / CLAUDE.md / MEMORY.md / Cursor rules", manual: "partial", generic: "partial", mg: "yes" },
  { capability: "Review-first transcript capture", manual: "no", generic: "partial", mg: "yes" },
  { capability: "Supersedes outdated project decisions", manual: "no", generic: "partial", mg: "yes" },
  { capability: "Secret-aware generated context", manual: "no", generic: "partial", mg: "yes" },
  { capability: "Local-first alpha flow", manual: "yes", generic: "partial", mg: "yes" },
  { capability: "No required external LLM API", manual: "yes", generic: "partial", mg: "yes" },
];

const workflow = [
  { step: "Capture", desc: "Point MemoryGuard at a transcript or session file.", cmd: "memoryguard capture file ./session.txt" },
  { step: "Review", desc: "Inspect pending candidates before they become durable context.", cmd: "memoryguard capture pending" },
  { step: "Approve", desc: "Promote only the facts that should survive future sessions.", cmd: "memoryguard capture approve --all" },
  { step: "Sync", desc: "Write clean context into the files your tools already inspect.", cmd: "memoryguard sync" },
];

const generatedFiles = ["AGENTS.md", "CLAUDE.md", "MEMORY.md", ".cursor/rules/memoryguard.mdc"];

/* ── Page ─────────────────────────────────────────────────── */

export default function HomePage() {
  return (
    <SiteFrame>
      <main>
        {/* Hero */}
        <section className="hero-centered container">
          <Badge>Open-source alpha</Badge>
          <h1>Stop re-teaching your codebase to AI.</h1>
          <p className="hero-subtitle">
            MemoryGuard keeps your project&apos;s agent context files accurate
            across sessions. Capture decisions, review what matters, supersede
            outdated facts, and sync clean context into AGENTS.md, CLAUDE.md,
            MEMORY.md, and Cursor rules.
          </p>
          <div className="hero-actions">
            <Button href={githubUrl} variant="primary">Star on GitHub</Button>
            <Button href="/docs/quickstart" variant="secondary">Read quickstart</Button>
            <Button href="#demo" variant="ghost">Run the demo</Button>
          </div>
          <div className="status-pills" aria-label="Project status">
            {statusPills.map((p) => <span key={p}>{p}</span>)}
          </div>
        </section>

        {/* Product mockup */}
        <section className="container">
          <ProductMockup />
        </section>

        {/* Trust strip */}
        <div className="trust-strip container">
          {trustItems.map((t) => <span key={t}>{t}</span>)}
        </div>

        {/* Bento features */}
        <section className="section container">
          <div className="section-title centered">
            <Badge>Core workflow</Badge>
            <h2>The context layer your coding tools are missing.</h2>
            <p>
              MemoryGuard is not a chatbot and not a generic notes app. It is a
              focused workflow for keeping agent-facing project context reviewed,
              current, and synced.
            </p>
          </div>
          <div className="bento-grid">
            {features.map((f) => (
              <BentoCard kicker={f.kicker} title={f.title} key={f.title} wide={f.wide} icon={f.icon}>
                {f.body}
              </BentoCard>
            ))}
          </div>
        </section>

        {/* Before / After */}
        <section className="section compact container">
          <div className="section-title centered">
            <Badge>Before and after</Badge>
            <h2>From stale notes to maintained context.</h2>
          </div>
          <BeforeAfter />
        </section>

        {/* Workflow */}
        <section className="section container">
          <div className="section-title centered">
            <Badge>Workflow</Badge>
            <h2>Capture. Review. Approve. Sync.</h2>
          </div>
          <div className="workflow-grid">
            {workflow.map((w, i) => (
              <article className="workflow-card" key={w.step}>
                <span>{i + 1}</span>
                <h3>{w.step}</h3>
                <p>{w.desc}</p>
                <code>{w.cmd}</code>
              </article>
            ))}
          </div>
        </section>

        {/* Demo / Install */}
        <section className="section container" id="demo">
          <div className="demo-layout">
            <div>
              <Badge>Install</Badge>
              <h2>One command. Then <code>memoryguard demo</code>.</h2>
              <div className="install-tabs">
                <div className="install-tab">
                  <h4>Windows (PowerShell)</h4>
                  <code>irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.ps1 | iex</code>
                </div>
                <div className="install-tab">
                  <h4>macOS / Linux</h4>
                  <code>curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.sh | bash</code>
                </div>
              </div>
              <p style={{ marginTop: 14, fontSize: "0.84rem" }}>
                Prerequisites: <code>git</code> + <code>uv</code> (no Node required for the CLI).
                After this finishes, <code>memoryguard</code> is on your PATH
                and works from any cwd &mdash; including <code>C:\Windows\System32</code>.
              </p>
              <p style={{ marginTop: 6, fontSize: "0.84rem" }}>
                Advanced (from a clone): <code style={{ color: "var(--cyan)" }}>git clone &amp;&amp; bash scripts/install-alpha.sh</code>{" "}
                &mdash; or <code style={{ color: "var(--cyan)" }}>uv run memoryguard demo</code> from a checkout.
              </p>
            </div>
            <Terminal title="memoryguard demo">
              {`$ memoryguard doctor
[ok] MemoryGuard CLI   running v0.1.0
[ok] Python            cpython 3.14.0
[ok] uv                on PATH
$ memoryguard demo
Agent Capture demo passed.
Extracted 10 candidates, approved 9, wrote 4 context files.`}
            </Terminal>
          </div>
        </section>

        {/* Generated files */}
        <section className="section compact container">
          <div className="section-title centered">
            <Badge>Context targets</Badge>
            <h2>The files your agents already read.</h2>
            <p>MemoryGuard writes managed context into familiar project files.</p>
          </div>
          <div className="file-grid">
            {generatedFiles.map((f) => (
              <div className="file-card" key={f}><span /><code>{f}</code></div>
            ))}
          </div>
        </section>

        {/* Positioning cards */}
        <PositioningSection />

        {/* Comparison table */}
        <ComparisonSection />

        {/* Why checklist */}
        <WhySection />

        {/* Final CTA */}
        <section className="final-cta container">
          <h2>Give your coding tools the context they keep asking for.</h2>
          <p>Start with the local alpha, run the demo, and inspect the generated context yourself.</p>
          <div className="hero-actions">
            <Button href={githubUrl} variant="primary">Star on GitHub</Button>
            <Button href="/docs/quickstart" variant="secondary">Read quickstart</Button>
          </div>
        </section>
      </main>
    </SiteFrame>
  );
}

/* ── Components ───────────────────────────────────────────── */

function ProductMockup() {
  return (
    <div className="mockup-wrapper">
      <div className="mockup">
        <div className="mockup-sidebar">
          <div className="mockup-sidebar-brand">MemoryGuard</div>
          <span>Capture</span>
          <span className="active">Pending</span>
          <span>Context Sync</span>
          <span>Generated Files</span>
        </div>
        <div className="mockup-main">
          <div className="mockup-main-title">Pending Review</div>
          <div className="candidate-card">
            <div className="candidate-title">Backend framework: Flask</div>
            <div className="candidate-meta">
              <span className="supersedes">Supersedes: FastAPI</span>
              <span className="source">codex-session.txt</span>
            </div>
          </div>
          <div className="candidate-card">
            <div className="candidate-title">Package manager: pnpm</div>
            <div className="candidate-meta">
              <span className="supersedes">Supersedes: npm</span>
              <span className="source">codex-session.txt</span>
            </div>
          </div>
          <div className="candidate-card redacted">
            <div className="candidate-title">Sensitive value omitted</div>
            <div className="candidate-meta">
              <span className="reason">Reason: secret-looking token</span>
            </div>
          </div>
        </div>
        <div className="mockup-panel">
          <div className="mockup-panel-title">Generated Context</div>
          <div className="gen-file">AGENTS.md</div>
          <div className="gen-file">CLAUDE.md</div>
          <div className="gen-file">MEMORY.md</div>
          <div className="gen-file">Cursor rules</div>
        </div>
        <div className="mockup-terminal">
          <span className="cmd">$ memoryguard capture approve --all</span><br />
          <span className="cmd">$ memoryguard sync</span><br />
          <span className="ok">✓ wrote 4 context files</span>
        </div>
      </div>
    </div>
  );
}

function BeforeAfter() {
  return (
    <div className="before-after">
      <article className="before-card">
        <h3>Before</h3>
        <ul>
          <li>This project uses FastAPI.</li>
          <li>This project uses npm.</li>
          <li>API key: sk-test-...</li>
        </ul>
      </article>
      <article className="after-card">
        <h3>After</h3>
        <ul>
          <li>Backend framework: Flask.</li>
          <li>Package manager: pnpm.</li>
          <li>FastAPI was previously used; superseded by Flask.</li>
          <li>Sensitive memory omitted from generated context.</li>
        </ul>
      </article>
    </div>
  );
}

function PositioningSection() {
  return (
    <section className="section container">
      <div className="section-title centered">
        <Badge>Positioning</Badge>
        <h2>Built for context files, not generic memory.</h2>
        <p>
          Most memory tools focus on storing or retrieving information. Manual
          agent files are simple, but they drift. MemoryGuard focuses on the
          practical middle: reviewed project memory that syncs into the files
          coding tools already read.
        </p>
      </div>
      <div className="positioning-grid">
        <article className="positioning-card">
          <h3>Manual agent files</h3>
          <ul>
            <li>easy to start</li>
            <li>drift over time</li>
            <li>copied across tools manually</li>
          </ul>
        </article>
        <article className="positioning-card">
          <h3>Generic memory layers</h3>
          <ul>
            <li>useful for retrieval</li>
            <li>often not context-file-first</li>
            <li>may be too broad for daily coding workflow</li>
          </ul>
        </article>
        <article className="positioning-card positive">
          <h3>MemoryGuard</h3>
          <ul>
            <li>review-first capture</li>
            <li>context-file sync</li>
            <li>supersession</li>
            <li>secret omission</li>
            <li>local-first alpha flow</li>
          </ul>
        </article>
      </div>
    </section>
  );
}

function CellIcon({ kind }: { kind: CellKind }) {
  if (kind === "yes") return <span className="ci ci-yes" aria-label="Yes">✓</span>;
  if (kind === "partial") return <span className="ci ci-partial" aria-label="Partial">◐</span>;
  return <span className="ci ci-no" aria-label="No">–</span>;
}

function ComparisonSection() {
  return (
    <section className="section container">
      <div className="section-title centered">
        <Badge>Comparison</Badge>
        <h2>Built for agent context, not generic notes.</h2>
      </div>
      <div className="comp-table-wrap">
        <table className="comp-table" role="table">
          <thead>
            <tr>
              <th scope="col">Capability</th>
              <th scope="col">Manual files</th>
              <th scope="col">Generic memory</th>
              <th scope="col">MemoryGuard</th>
            </tr>
          </thead>
          <tbody>
            {compRows.map((row) => (
              <tr key={row.capability}>
                <td className="comp-cap">{row.capability}</td>
                <td className="comp-cell"><CellIcon kind={row.manual} /></td>
                <td className="comp-cell"><CellIcon kind={row.generic} /></td>
                <td className="comp-cell comp-mg"><CellIcon kind={row.mg} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="comp-footer-note">
        MemoryGuard&apos;s advantage is not being the biggest memory system.
        Its advantage is being the focused Context Sync layer for the files
        coding agents already read.
      </p>
    </section>
  );
}

function WhySection() {
  return (
    <section className="section compact container">
      <div className="checklist-card">
        <div className="checklist-left">
          <div className="section-title">
            <Badge>Why MemoryGuard</Badge>
            <h2>Purpose-built for agent context.</h2>
          </div>
          <ul className="checklist-list">
            {whyItems.map((r) => (
              <li key={r}>
                <span className="ci ci-yes" aria-hidden>✓</span>
                {r}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
