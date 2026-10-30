import "./TypographyIdeasPage.css";

export type TypographyStageView = "jobs" | "ideas" | "style-lab";

export function Stage1Subnav({ view, onView, ideasCount }: {
  view: TypographyStageView;
  onView: (next: TypographyStageView) => void;
  ideasCount: number;
}) {
  return <nav className="typography-stage1-subnav" aria-label="Stage 1 views" data-testid="typography-stage1-subnav">
    <button type="button" onClick={() => onView("jobs")} aria-current={view === "jobs" ? "page" : undefined}
      className={view === "jobs" ? "active" : ""}>Keyword Jobs</button>
    <button type="button" onClick={() => onView("ideas")} aria-current={view === "ideas" ? "page" : undefined}
      className={view === "ideas" ? "active" : ""}>Typography Ideas <span>{ideasCount}</span></button>
    <button type="button" onClick={() => onView("style-lab")}
      aria-current={view === "style-lab" ? "page" : undefined}
      className={view === "style-lab" ? "active" : ""}>Style Lab <span>MVP</span></button>
  </nav>;
}
