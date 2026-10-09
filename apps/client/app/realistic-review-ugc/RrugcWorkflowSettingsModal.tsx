import { useEffect } from "react";
import { PinterestAutoScoutPanel } from "./PinterestAutoScoutPanel";
import { RrugcHealthPanel } from "./RrugcHealthPanel";
import { WorkflowStatusIcon } from "./WorkflowStatusIcon";

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
        <div className="rrugc-settings-modal-heading">
          <span className="rrugc-settings-heading-icon"><WorkflowStatusIcon name="settings" size={20} /></span>
          <div>
            <small>WORKFLOW / MONITORING</small>
            <h2 id="rrugc-settings-modal-title">Scout & automation</h2>
            <p>Pipeline health, discovery performance and connected Scout machines</p>
          </div>
        </div>
        <button
          type="button"
          className="rrugc-skill-modal-close"
          onClick={onClose}
          aria-label="Close workflow settings"
        >×</button>
      </header>
      <div className="rrugc-settings-modal-body">
        <RrugcHealthPanel onError={onError} />
        <PinterestAutoScoutPanel onError={onError} />
      </div>
    </section>
  </div>;
}
