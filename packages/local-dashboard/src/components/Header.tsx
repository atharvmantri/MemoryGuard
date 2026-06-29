export interface HeaderProps {
  /** Store mode shown in the header pill (Phase 1 is always "local"). */
  mode?: string;
}

/**
 * Top nav for the OSS local dashboard. Uses the Dark logo variant because the
 * header surface is light (white) — per design.md ("use Dark variant on light
 * surfaces"). Asset copied to public/brand/ in task 1.
 */
export function Header({ mode = "local" }: HeaderProps) {
  return (
    <header className="mg-header">
      <img
        className="mg-header__logo"
        src="/brand/MemoryGuard_Primary_Logo_Dark.png"
        alt="MemoryGuard"
      />
      <h1 className="mg-header__title">Local Dashboard</h1>
      <span className="mg-header__mode" title="Storage mode">
        mode: {mode}
      </span>
    </header>
  );
}

export default Header;
