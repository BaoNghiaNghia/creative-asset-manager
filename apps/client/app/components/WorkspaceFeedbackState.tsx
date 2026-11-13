import type { ReactNode } from "react";

export type WorkspaceFeedbackKind = "loading" | "empty" | "error";

type FeedbackProps = {
  kind: WorkspaceFeedbackKind;
  title: string;
  detail?: string;
  onRetry?: () => void;
  actionLabel?: string;
  icon?: ReactNode;
  compact?: boolean;
};

/** A visible, accessible operation state. Does not remount any parent panel. */
export function WorkspaceFeedbackState({
  kind, title, detail, onRetry, actionLabel = "Try again", icon, compact = false,
}: FeedbackProps) {
  const loading = kind === "loading";
  return <div
    className={"cam-workspace-feedback cam-workspace-feedback--" + kind + (compact ? " is-compact" : "")}
    role={kind === "error" ? "alert" : "status"}
    aria-live={kind === "error" ? "assertive" : "polite"}
    aria-busy={loading}
  >
    <span className="cam-workspace-feedback-icon" aria-hidden="true">
      {icon ?? (loading ? <span className="cam-workspace-feedback-spinner" /> : kind === "error" ? "!" : "—")}
    </span>
    <div className="cam-workspace-feedback-copy">
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
    </div>
    {!loading && onRetry && <button className="cam-workspace-feedback-action" type="button" onClick={onRetry}>{actionLabel}</button>}
  </div>;
}

export function WorkspaceSkeletonRows({ rows = 4 }: { rows?: number }) {
  const count = Math.max(1, Math.min(12, Math.trunc(rows)));
  return <div className="cam-workspace-skeleton-rows" role="status" aria-live="polite" aria-busy="true" aria-label="Loading records">
    <span className="sr-only">Loading records…</span>
    {Array.from({ length: count }, (_, index) => <span key={index} className="cam-workspace-skeleton-row" aria-hidden="true">
      <i className="cam-workspace-skeleton-avatar" /><i className="cam-workspace-skeleton-primary" /><i className="cam-workspace-skeleton-secondary" />
    </span>)}
  </div>;
}
