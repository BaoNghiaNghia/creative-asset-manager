import { useEffect, useRef, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import { BrandIcon } from "./Icons";
import { WorkspaceNavigation, WorkspaceNavigationIcon, mayViewReviewBoard, type WorkspaceRoute } from "./WorkspaceNavigation";

type MobileDestination = { id: WorkspaceRoute; href: string; title: string; shortTitle: string };

const ALL_MOBILE_DESTINATIONS: readonly MobileDestination[] = [
  { id: "assets", href: "/", title: "Asset Explorer", shortTitle: "Assets" },
  { id: "operations", href: "/ai-operations", title: "AI Operations", shortTitle: "AI Ops" },
  { id: "realistic-review-ugc", href: "/realistic-review-ugc", title: "Realistic Review UGC", shortTitle: "UGC" },
  { id: "review-board", href: "/review-board", title: "Review Board", shortTitle: "Reviews" },
  { id: "generation", href: "/video-generation", title: "Video Generation", shortTitle: "Video" },
  { id: "queue", href: "/job-queue", title: "Job Queue", shortTitle: "Queue" },
  { id: "access", href: "/settings/access", title: "Access Management", shortTitle: "Access" },
];

/** Navigation on phones uses the same permission model as the desktop sidebar. */
export function availableMobileDestinations(permissions: readonly string[]): readonly MobileDestination[] {
  return ALL_MOBILE_DESTINATIONS.filter(item => {
    if (item.id === "operations" || item.id === "queue") return permissions.includes("ai_operations.read");
    if (item.id === "realistic-review-ugc") return permissions.includes("realistic_review_ugc.read");
    if (item.id === "review-board") return mayViewReviewBoard(permissions);
    if (item.id === "access") return permissions.includes("tenant_members.read");
    return true;
  });
}

export function splitMobileDestinations(destinations: readonly MobileDestination[]) {
  // Keep the four most-used destinations stable as permissions load and routes change.
  return { tabs: destinations.slice(0, 4), more: destinations.slice(4) };
}

export function ResponsiveWorkspaceNav({ active }: { active: WorkspaceRoute }) {
  const [permissions, setPermissions] = useState<readonly string[]>([]);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreButton = useRef<HTMLButtonElement>(null);
  const morePanel = useRef<HTMLElement>(null);
  const firstMoreLink = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity()
      .then(identity => { if (alive) setPermissions(identity.permissions); })
      .catch(() => { if (alive) setPermissions([]); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!moreOpen) return;
    firstMoreLink.current?.focus();
    const previousOverflow = document.body.style.overflowY;
    document.body.style.overflowY = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMoreOpen(false);
        moreButton.current?.focus();
        return;
      }
      if (event.key !== "Tab" || !morePanel.current) return;
      const focusable = Array.from(morePanel.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled])'));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflowY = previousOverflow;
    };
  }, [moreOpen]);

  const destinations = availableMobileDestinations(permissions);
  const { tabs, more } = splitMobileDestinations(destinations);
  const selectedInMore = more.some(item => item.id === active);

  return <>
    <div className="responsive-workspace-nav">
      <a className="responsive-workspace-brand" href="/" aria-label="Creative Asset Manager">
        <span><BrandIcon /></span>
        <strong>Creative Asset Manager</strong>
      </a>
      <WorkspaceNavigation
        active={active}
        showOperations={permissions.includes("ai_operations.read")}
        showReviewBoard={mayViewReviewBoard(permissions)}
        permissions={permissions}
      />
      <nav className="mobile-workspace-tabs" aria-label="Mobile workspace navigation">
        {tabs.map(item => <a
          key={item.id}
          href={item.href}
          className={"mobile-workspace-tab" + (active === item.id ? " active" : "")}
          aria-current={active === item.id ? "page" : undefined}
          aria-label={item.title}
          title={item.title}
        >
          <WorkspaceNavigationIcon name={item.id} />
          <span>{item.shortTitle}</span>
        </a>)}
        {more.length > 0 && <button
          ref={moreButton}
          type="button"
          className={"mobile-workspace-tab mobile-workspace-more" + (selectedInMore || moreOpen ? " active" : "")}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          aria-controls="mobile-workspace-more-dialog"
          aria-label="More workspaces"
          onClick={() => setMoreOpen(value => !value)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" />
          </svg>
          <span>More</span>
        </button>}
      </nav>
    </div>
    {moreOpen && more.length > 0 && <div className="mobile-workspace-more-layer">
      <button className="mobile-workspace-more-scrim" type="button" onClick={() => setMoreOpen(false)} aria-label="Close workspace menu" />
      <section ref={morePanel} id="mobile-workspace-more-dialog" className="mobile-workspace-more-panel" role="dialog" aria-modal="true" aria-label="More workspaces">
        <header className="mobile-workspace-more-heading">
          <div>
            <strong>Workspaces</strong>
            <small>Switch to another area</small>
          </div>
          <button type="button" aria-label="Close workspace menu" onClick={() => { setMoreOpen(false); moreButton.current?.focus(); }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </header>
        <div className="mobile-workspace-more-items">
          {more.map((item, index) => <a
            key={item.id}
            ref={index === 0 ? firstMoreLink : undefined}
            href={item.href}
            className={active === item.id ? "active" : undefined}
            aria-current={active === item.id ? "page" : undefined}
          >
            <WorkspaceNavigationIcon name={item.id} />
            <span>{item.title}</span>
            <svg className="mobile-workspace-more-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
          </a>)}
        </div>
      </section>
    </div>}
  </>;
}
