import Image from "next/image";
import Link from "next/link";

const githubUrl = "https://github.com/atharvmantri/MemoryGuard";
const publicAssetPath =
  process.env.MEMORYGUARD_GITHUB_PAGES === "true" ? "/MemoryGuard" : "";
const licenseUrl = `${githubUrl}/blob/main/LICENSE`;
const codeOfConductUrl = `${githubUrl}/blob/main/CODE_OF_CONDUCT.md`;
const pagesUrl = "https://pages.github.com/";

const docsLinks = [
  ["/docs", "Overview"],
  ["/docs/quickstart", "Quickstart"],
  ["/docs/agent-capture", "Agent Capture"],
  ["/docs/context-sync", "Context Sync"],
  ["/docs/security", "Security"],
] as const;

export function SiteFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="site-shell">
      <header className="topbar">
        <nav className="nav" aria-label="Main navigation">
          <Link className="brand" href="/" aria-label="MemoryGuard home">
            <Image
              src={`${publicAssetPath}/MemoryGuard_Icon_256.png`}
              width={64}
              height={64}
              alt=""
              priority
            />
            <span>MemoryGuard</span>
          </Link>
          <div className="nav-links">
            <Link href="/docs">Docs</Link>
            <Link href="/docs/quickstart">Quickstart</Link>
            <a href={githubUrl}>GitHub</a>
          </div>
          <a className="nav-cta" href={githubUrl}>
            Star on GitHub
          </a>
          <div className="nav-mobile-cta">
            <a className="nav-cta" href={githubUrl}>
              Star
            </a>
          </div>
        </nav>
      </header>
      {children}
      <Footer />
    </div>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="container">
        <div>
          MemoryGuard public OSS alpha. Licensed under{" "}
          <a href={licenseUrl}>Apache-2.0</a>. Non-commercial project.
        </div>
        <div className="footer-links">
          <a href={githubUrl}>GitHub</a>
          <Link href="/docs">Docs</Link>
          <Link href="/docs/quickstart">Quickstart</Link>
          <Link href="/docs/security">Security</Link>
          <a href={codeOfConductUrl}>Code of Conduct</a>
          <a href={pagesUrl}>Published with GitHub Pages</a>
        </div>
      </div>
    </footer>
  );
}

export function Badge({ children }: { children: React.ReactNode }) {
  return (
    <div className="badge">
      <span className="dot" />
      {children}
    </div>
  );
}

export function Button({
  href,
  children,
  variant = "secondary",
}: {
  href: string;
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "ghost";
}) {
  const className = `button ${variant}`;
  if (href.startsWith("http") || href.startsWith("#")) {
    return (
      <a className={className} href={href}>
        {children}
      </a>
    );
  }

  return (
    <Link className={className} href={href}>
      {children}
    </Link>
  );
}

export function Terminal({
  title = "memoryguard alpha",
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="terminal">
      <div className="terminal-head">
        <span />
        <span />
        <span />
        <strong>{title}</strong>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export function BentoCard({
  kicker,
  title,
  children,
  wide = false,
  icon,
}: {
  kicker: string;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
  icon?: string;
}) {
  return (
    <article className={`bento-card${wide ? " wide" : ""}`}>
      <div className="bento-kicker">{kicker}</div>
      <h3>{title}</h3>
      <p>{children}</p>
      {icon && <div className="bento-icon">{icon}</div>}
    </article>
  );
}

export function PageHero({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="docs-hero">
      <h1>{title}</h1>
      <p>{children}</p>
    </section>
  );
}

export function DocsLayout({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <main className="docs-shell container">
      <aside className="doc-nav" aria-label="Documentation">
        <div className="doc-nav-title">Docs</div>
        {docsLinks.map(([href, label]) => (
          <Link href={href} key={href}>
            {label}
          </Link>
        ))}
      </aside>
      <article className="docs-content">
        <PageHero title={title}>{description}</PageHero>
        <div className="docs-body">{children}</div>
      </article>
    </main>
  );
}

export function CodeBlock({ children }: { children: string }) {
  return <Terminal title="shell">{children}</Terminal>;
}

export function Callout({
  type = "note",
  title,
  children,
}: {
  type?: "alpha" | "warning" | "note";
  title: string;
  children: React.ReactNode;
}) {
  return (
    <aside className={`callout ${type}`}>
      <strong>{title}</strong>
      <p>{children}</p>
    </aside>
  );
}

export { githubUrl };
