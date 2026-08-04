import { Callout, CodeBlock, DocsLayout, SiteFrame } from "../../site";

export default function QuickstartPage() {
  return (
    <SiteFrame>
      <DocsLayout
        title="Quickstart"
        description="Install MemoryGuard in one command during the public alpha. The bootstrap clones the repo, runs uv sync, and writes the memoryguard command to your PATH."
      >
        <Callout type="alpha" title="Alpha install path">
          There is no PyPI or npm package to install during the public alpha.
          The supported path is the one-command bootstrap below, which clones
          the public repo into a stable source directory, runs{" "}
          <code>uv sync</code>, and wires up a direct <code>memoryguard</code>{" "}
          command on your <code>PATH</code>. Hosted sync, team features, and
          team SSO are not included in the alpha.
        </Callout>

        <h2>1. One-command install (recommended)</h2>
        <p>
          Prerequisites: <code>git</code> and <a href="https://docs.astral.sh/uv/">uv</a>.
          Node and pnpm are <strong>not</strong> required for the CLI — they are
          only needed for the local dashboard and the TypeScript SDK. The
          bootstrap prints clear install instructions for either missing tool
          and exits non-zero rather than partially installing.
        </p>
        <CodeBlock>{`# Windows (PowerShell)
irm https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.ps1 | iex

# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/atharvmantri/MemoryGuard/main/scripts/install.sh | bash`}</CodeBlock>
        <p>
          After this finishes, the wrapper is on your <code>PATH</code> and{" "}
          <code>memoryguard doctor</code> plus <code>memoryguard demo</code>{" "}
          work from any shell, including protected cwds like{" "}
          <code>C:\\Windows\\System32</code>.
        </p>

        <h2>2. Use the <code>memoryguard</code> command directly</h2>
        <CodeBlock>{`memoryguard doctor
memoryguard demo
memoryguard init
memoryguard remember "This project uses Flask for the backend."
memoryguard sync
memoryguard status`}</CodeBlock>
        <p>
          <code>memoryguard doctor</code> prints a pass / warn / error table
          that confirms the install, the Python environment, the local store,
          and the optional tools. <code>memoryguard demo</code> runs the Agent
          Capture flow against a temporary project and then cleans itself up.
        </p>

        <h2>3. Agent Capture in one project</h2>
        <CodeBlock>{`memoryguard capture file ./codex-session.txt --source codex
memoryguard capture pending
memoryguard capture approve --all
memoryguard sync`}</CodeBlock>

        <h2>4. Uninstall the wrapper</h2>
        <p>
          The uninstaller removes the wrapper files (and the persistent
          user-PATH entry the installer added). User project{" "}
          <code>.memoryguard/</code> stores are never touched. Add{" "}
          <code>-RemoveSource</code> / <code>--remove-source</code> to also delete
          the cloned source directory.
        </p>
        <CodeBlock>{`# Windows
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\\MemoryGuard\\source\\scripts\\uninstall.ps1"

# macOS / Linux
bash ~/.local/share/memoryguard/source/scripts/uninstall.sh`}</CodeBlock>

        <h2>Troubleshooting PATH</h2>
        <p>
          If <code>memoryguard</code> does not resolve in a new shell, check
          which one Windows or POSIX is finding first:
        </p>
        <CodeBlock>{`# Windows PowerShell
Get-Command memoryguard -All

# cmd.exe
where memoryguard`}</CodeBlock>
        <p>
          Expected: <code>%LOCALAPPDATA%\\Programs\\MemoryGuard\\memoryguard.cmd</code>{" "}
          (or <code>memoryguard.ps1</code>).
        </p>
        <p>
          If a stale <code>memoryguard.exe</code> from a prior Python install
          (e.g. <code>C:\\Users\\&lt;you&gt;\\AppData\\Local\\Programs\\Python\\Python312\\Scripts\\memoryguard.exe</code>)
          still wins on <code>PATH</code>, re-run the install with{" "}
          <code>-RemoveShadowingCommands</code> to move the offending file out
          of the way automatically.
        </p>

        <h2>Advanced: from source without the wrapper</h2>
        <p>
          If you would rather not install the wrapper, every command also works
          directly with <code>uv run</code> from a clone of the repo:
        </p>
        <CodeBlock>{`git clone https://github.com/atharvmantri/MemoryGuard.git
cd MemoryGuard
uv run memoryguard demo`}</CodeBlock>
        <p>
          This is the path used during active development. See{" "}
          <a href="https://github.com/atharvmantri/MemoryGuard/blob/main/CONTRIBUTING.md">
            CONTRIBUTING.md
          </a>{" "}
          for the full dev loop, including the MCP server, the REST API, and
          the local dashboard.
        </p>
      </DocsLayout>
    </SiteFrame>
  );
}
