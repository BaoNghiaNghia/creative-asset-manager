import { useEffect } from "react";
import { PinterestAutoScoutPanel } from "./PinterestAutoScoutPanel";

export function RrugcWorkflowSettingsModal({
  open,
  onClose,
  onError,
}: {
  open: boolean;
  onClose: () => void;
  onError: (message: string) => void;
}) {
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open) return null;

  return <div
    className="rrugc-settings-modal-backdrop"
    role="presentation"
    onMouseDown={event => event.target === event.currentTarget && onClose()}
  >
    <section
      className="rrugc-settings-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rrugc-settings-modal-title"
    >
      <header className="rrugc-settings-modal-header">
        <div>
          <small>WORKFLOW SETTINGS</small>
          <h2 id="rrugc-settings-modal-title">Scout & automation settings</h2>
          <p>Global controls live outside the stage sequence so Stage 0–4 stay focused on production work.</p>
        </div>
        <button
          type="button"
          className="rrugc-skill-modal-close"
          onClick={onClose}
          aria-label="Close workflow settings"
        >×</button>
      </header>
      <div className="rrugc-settings-modal-body">
        <PinterestAutoScoutPanel onError={onError} />
      </div>
    </section>
  </div>;
}
