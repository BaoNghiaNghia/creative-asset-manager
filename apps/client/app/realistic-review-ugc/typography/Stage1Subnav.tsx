import "./TypographyIdeasPage.css";

export function Stage1Subnav({ view, onView, ideasCount }: {
  view: "jobs" | "ideas";
  onView: (next: "jobs" | "ideas") => void;
  ideasCount: number;
}) {
  return <nav className="typography-stage1-subnav" aria-label="Stage 1 views" data-testid="typography-stage1-subnav">
    <button type="button" onClick={() => onView("jobs")} aria-current={view === "jobs" ? "page" : undefined}
      className={view === "jobs" ? "active" : ""}>Keyword Jobs</button>
    <button type="button" onClick={() => onView("ideas")} aria-current={view === "ideas" ? "page" : undefined}
      className={view === "ideas" ? "active" : ""}>Typography Ideas <span>{ideasCount}</span></button>
    <span className="typography-stage1-subnav-hint" title="Style Lab will become available after its backend QA and rollout gates">Style Lab · Later</span>
  </nav>;
}
