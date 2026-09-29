import { useEffect, useMemo, useState } from "react";

export type UpdateStatus = "disabled" | "idle" | "checking" | "available" | "downloading" | "ready" | "up-to-date" | "error";

export type UpdateState = {
  status: UpdateStatus;
  currentVersion: string;
  availableVersion?: string;
  percent?: number;
  message?: string;
};

export function desktopUpdateProgress(percent: number | undefined): number {
  if (!Number.isFinite(percent)) return 0;
  return Math.max(0, Math.min(100, Math.round(percent || 0)));
}

export function desktopUpdateLabel(state: UpdateState): string {
  switch (state.status) {
    case "checking": return "Checking for updates";
    case "available": return state.availableVersion ? `Update ${state.availableVersion} available` : "Update available";
    case "downloading": return `Downloading ${desktopUpdateProgress(state.percent)}%`;
    case "ready": return "Update ready";
    case "error": return "Update failed";
    default: return "";
  }
}

function UpdateGlyph({ status }: { status: UpdateStatus }) {
  if (status === "ready") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7.5 12.5 3 3 6-7" /><circle cx="12" cy="12" r="8.5" /></svg>;
  }
  if (status === "error") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 7.5v5" /><path d="M12 16.5h.01" /><circle cx="12" cy="12" r="8.5" /></svg>;
  }
  if (status === "checking") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 8a8 8 0 1 0 .4 7" /><path d="M19 4v4h-4" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V6" /><path d="m8.5 9.5 3.5-3.5 3.5 3.5" /><path d="M6 18h12" /></svg>;
}

export function DesktopUpdateNotice() {
  const updates = window.camDesktop?.updates;
  const [state, setState] = useState<UpdateState | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  useEffect(() => {
    if (!updates) return;
    let active = true;
    void updates.getState().then(next => { if (active) setState(next); });
    const unsubscribe = updates.onState(next => {
      if (!active) return;
      setState(next);
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [updates]);

  useEffect(() => {
    if (!state) return;
    if (state.status === "available" || state.status === "ready" || state.status === "error") {
      setPanelOpen(true);
    }
  }, [state?.status, state?.availableVersion]);

  const visible = !!updates && !!state && ["checking", "available", "downloading", "ready", "error"].includes(state.status);
  const percent = desktopUpdateProgress(state?.percent);
  const version = state?.availableVersion || state?.currentVersion || "";
  const tone = state?.status === "ready" ? "is-ready" : state?.status === "error" ? "is-error" : "is-active";
  const chipLabel = useMemo(() => state ? desktopUpdateLabel(state) : "", [state]);

  if (!updates || !state || !visible) return null;

  const title = state.status === "ready"
    ? "Update ready"
    : state.status === "error"
      ? "Couldn’t update"
      : state.status === "available"
        ? "New version available"
        : "Downloading update";

  const detail = state.status === "ready"
    ? `Version ${version} is ready to install.`
    : state.status === "error"
      ? "The update couldn’t be completed. Try again in a moment."
      : state.status === "available"
        ? `Creative Asset Manager ${state.currentVersion} → ${version}`
        : version
          ? `Version ${version}`
          : "Downloading securely in the background";

  return <>
    <button
      className={`desktop-update-chip ${tone} ${state.status === "checking" ? "is-checking" : ""}`}
      type="button"
      aria-label={panelOpen ? "Hide update details" : "Show update details"}
      onClick={() => state.status !== "checking" && setPanelOpen(open => !open)}
    >
      <span className="desktop-update-chip__icon"><UpdateGlyph status={state.status} /></span>
      <span>{chipLabel}</span>
      {state.status === "downloading" && <span className="desktop-update-chip__percent">{percent}%</span>}
    </button>

    {panelOpen && state.status !== "checking" && <aside className={`desktop-update-card ${tone}`} role="status" aria-live="polite">
      <div className="desktop-update-card__head">
        <span className="desktop-update-card__icon" aria-hidden="true"><UpdateGlyph status={state.status} /></span>
        <div>
          <strong>{title}</strong>
          <small>{detail}</small>
        </div>
        <button className="desktop-update-card__close" type="button" aria-label="Hide update details" onClick={() => setPanelOpen(false)}>×</button>
      </div>

      {(state.status === "available" || state.status === "downloading") && <div className="desktop-update-card__progress">
        <div className="desktop-update-card__progress-meta">
          <span>{percent >= 100 ? "Finalizing…" : "Downloading in the background"}</span>
          <b>{percent}%</b>
        </div>
        <span className="desktop-update-progress" aria-hidden="true"><i style={{ width: `${Math.max(percent, state.status === "available" ? 2 : 0)}%` }} /></span>
      </div>}

      {state.status === "ready" && <div className="desktop-update-card__actions">
        <button className="desktop-update-primary" type="button" onClick={() => void updates.restartAndInstall()}>Restart now</button>
        <button className="desktop-update-secondary" type="button" onClick={() => setPanelOpen(false)}>Later</button>
      </div>}

      {state.status === "error" && <div className="desktop-update-card__actions">
        <button className="desktop-update-primary" type="button" onClick={() => void updates.check()}>Retry</button>
        <button className="desktop-update-secondary" type="button" onClick={() => setPanelOpen(false)}>Dismiss</button>
      </div>}

      {state.status === "error" && state.message && <details className="desktop-update-card__error"><summary>Details</summary><p>{state.message}</p></details>}
    </aside>}
  </>;
}
