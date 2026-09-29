import { useEffect, useState } from "react";

type UpdateState = {
  status: "disabled" | "idle" | "checking" | "available" | "downloading" | "ready" | "up-to-date" | "error";
  currentVersion: string;
  availableVersion?: string;
  percent?: number;
  message?: string;
};

export function DesktopUpdateNotice() {
  const updates = window.camDesktop?.updates;
  const [state, setState] = useState<UpdateState | null>(null);
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);

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
    setDismissedVersion(null);
  }, [state?.availableVersion]);

  if (!updates || !state) return null;
  if (!["available", "downloading", "ready"].includes(state.status)) return null;
  if (dismissedVersion && dismissedVersion === state.availableVersion) return null;

  const version = state.availableVersion ? ` v${state.availableVersion}` : "";
  const downloading = state.status === "available" || state.status === "downloading";
  const percent = Math.round(state.percent || 0);

  return <aside className="desktop-update-notice" role="status" aria-live="polite">
    <span className="desktop-update-notice__icon" aria-hidden="true">↑</span>
    <div className="desktop-update-notice__body">
      <strong>{state.status === "ready" ? `Creative Asset Manager${version} is ready` : `Downloading Creative Asset Manager${version}`}</strong>
      <small>{state.status === "ready"
        ? "Restart now to finish the update, or close the app later and it will install automatically."
        : `Downloading securely in the background${percent > 0 ? ` · ${percent}%` : "…"}`}</small>
      {downloading && <span className="desktop-update-progress" aria-hidden="true"><i style={{ width: `${percent}%` }} /></span>}
    </div>
    {state.status === "ready" && <button className="desktop-update-primary" type="button" onClick={() => void updates.restartAndInstall()}>Restart &amp; update</button>}
    <button className="desktop-update-dismiss" type="button" aria-label="Dismiss update notification" onClick={() => setDismissedVersion(state.availableVersion || state.currentVersion)}>×</button>
  </aside>;
}
