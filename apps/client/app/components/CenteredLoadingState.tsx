export type CenteredLoadingKind = "permissions" | "application";

export function CenteredLoadingState({
  kind,
  title,
  detail,
  layout = "viewport",
}: {
  kind: CenteredLoadingKind;
  title: string;
  detail?: string;
  layout?: "viewport" | "panel";
}) {
  return <div
    className={`centered-loading-state centered-loading-state--${layout} centered-loading-state--${kind}`}
    role="status"
    aria-live="polite"
    aria-busy="true"
  >
    <div className="centered-loading-state__content">
      <span className="centered-loading-state__icon" aria-hidden="true">
        <span className="centered-loading-state__spinner" />
        {kind === "permissions"
          ? <svg viewBox="0 0 24 24" fill="none">
            <path d="M12 3.1 19.2 6v5.25c0 4.65-2.95 8.05-7.2 9.55-4.25-1.5-7.2-4.9-7.2-9.55V6L12 3.1Z" />
            <circle cx="10.05" cy="9.35" r="2.05" />
            <path d="M6.95 14.2c.65-1.62 1.7-2.45 3.1-2.45 1.02 0 1.9.45 2.53 1.33" />
            <path d="m14.25 15.1 1.2 1.2 2.25-2.45" />
          </svg>
          : <svg viewBox="0 0 24 24" fill="none">
            <rect x="3.4" y="4.1" width="17.2" height="15.8" rx="3" />
            <path d="M3.8 8.2h16.4" />
            <path d="M8.2 8.5v11" />
            <path d="M11.4 11.4h5.7M11.4 14.7h4.2" />
          </svg>}
      </span>
      <strong>{title}</strong>
      {detail && <span className="centered-loading-state__detail">{detail}</span>}
    </div>
  </div>;
}
