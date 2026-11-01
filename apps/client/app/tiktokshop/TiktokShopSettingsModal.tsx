import { useEffect, useRef } from "react";
import { WorkflowStatusIcon } from "../realistic-review-ugc/WorkflowStatusIcon";
import { TiktokShopAgentPanel } from "./TiktokShopAgentPanel";

/** Settings shared by Stage 3–5. Pairing credentials exist only while open. */
export function TiktokShopSettingsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open) return null;

  return <div className="rrugc-settings-modal-backdrop"
    role="presentation"
    onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
    <section className="rrugc-settings-modal tiktokshop-settings-modal"
      role="dialog" aria-modal="true" aria-labelledby="tiktokshop-settings-title">
      <header className="rrugc-settings-modal-header">
        <div className="rrugc-settings-modal-heading">
          <span className="rrugc-settings-heading-icon">
            <WorkflowStatusIcon name="settings" size={20} />
          </span>
          <div>
            <small>TIKTOKSHOP / SCOUT &amp; UGC</small>
            <h2 id="tiktokshop-settings-title">Settings</h2>
            <p>Windows Scout pairing and Stage 3 Drive import configuration</p>
          </div>
        </div>
        <button ref={closeButtonRef} type="button" className="rrugc-skill-modal-close"
          onClick={onClose} aria-label="Close TikTokShop settings">×</button>
      </header>
      <div className="rrugc-settings-modal-body">
        <TiktokShopAgentPanel />
        <section className="rrugc-card tiktokshop-stage-note" aria-label="Stage 3 Drive import rules">
          <strong>Stage 3 · TikTokShop Drive</strong>
          <p>Source photos are indexed automatically on first open without AI jobs.
            <strong> Scan now</strong> queues at most 12 product-context analyses.
            Only a paired TikTokShop Windows Agent receives TikTokShop Pinterest campaigns.</p>
        </section>
      </div>
    </section>
  </div>;
}
