import { BrandIcon } from "./Icons";
import "./UnderConstructionPage.css";
import "./WorkspaceConnectionState.css";

export type WorkspaceConnectionMode = "reconnecting" | "unauthenticated" | "denied";

export function WorkspaceConnectionState({
  mode,
  onRetry,
}: {
  mode: WorkspaceConnectionMode;
  onRetry?: () => void;
}) {
  const reconnecting = mode === "reconnecting";
  const unauthenticated = mode === "unauthenticated";
  const title = reconnecting ? "Connection interrupted"
    : unauthenticated ? "Session expired"
      : "Access restricted";
  const detail = reconnecting
    ? "We couldn't verify your workspace access. We'll retry automatically and keep you on this page."
    : unauthenticated
      ? "Your sign-in session is no longer available. Sign in again to continue to this workspace."
      : "Your account does not have permission to open this workspace. Contact an administrator if you need access.";

  return <main className="cam-under-construction cam-workspace-connection">
    <a className="cam-under-brand" href="/" aria-label="Creative assets - Asset Explorer">
      <span className="cam-under-brand-mark"><BrandIcon /></span>
      <span>Creative assets</span>
    </a>

    <section className="cam-workspace-connection__body" aria-labelledby="cam-connection-title">
      <div className={"cam-workspace-connection__illustration" + (reconnecting ? " is-reconnecting" : "")} aria-hidden="true">
        <div className="cam-workspace-connection__halo" />
        <svg className="cam-workspace-connection__symbol" viewBox="0 0 96 96" fill="none" aria-hidden="true">
          <rect x="17" y="22" width="62" height="50" rx="12" stroke="currentColor" strokeWidth="3" />
          <path d="M17 35h62" stroke="currentColor" strokeWidth="3" />
          <circle cx="25" cy="29" r="2.2" fill="currentColor" />
          <circle cx="33" cy="29" r="2.2" fill="currentColor" />
          <path d="m38 57 9-10 10 10m0-10v10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="70" cy="67" r="13" fill="#f7faff" stroke="currentColor" strokeWidth="3" />
          <path d="M70 60v8m0 5v.2" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        </svg>
        <span className="cam-workspace-connection__dot cam-workspace-connection__dot--one" />
        <span className="cam-workspace-connection__dot cam-workspace-connection__dot--two" />
      </div>

      <div className="cam-under-eyebrow">
        <span aria-hidden="true" className="cam-under-dot" />
        {reconnecting ? "WORKSPACE CONNECTION" : unauthenticated ? "SIGN-IN REQUIRED" : "ACCESS MANAGEMENT"}
      </div>
      <h1 id="cam-connection-title" className="cam-workspace-connection__title">{title}<span>.</span></h1>
      <p className="cam-under-detail cam-workspace-connection__detail">{detail}</p>

      {reconnecting && <div className="cam-workspace-connection__status" role="status" aria-live="polite">
        <span className="cam-workspace-connection__spinner" aria-hidden="true" />
        Retrying your connection automatically
      </div>}

      <div className="cam-workspace-connection__actions">
        {reconnecting && <button type="button" className="cam-under-button" onClick={onRetry}>
          Retry connection
          <svg className="cam-under-back-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M4 11a8 8 0 1 0 2-5M4 4v5h5" />
          </svg>
        </button>}
        {!reconnecting && <a className="cam-under-button" href="/">{unauthenticated ? "Return to sign in" : "Back to Asset Explorer"}
          <svg className="cam-under-back-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M5 15 15 5M6 5h9v9" /></svg>
        </a>}
        {reconnecting && <a href="/" className="cam-workspace-connection__secondary">Back to Asset Explorer</a>}
      </div>

      {reconnecting && <p className="cam-workspace-connection__note">The current page URL will be preserved while reconnecting.</p>}
    </section>

    <footer className="cam-under-footer">Creative Asset Management · Made for what comes next</footer>
  </main>;
}
