import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getStage1DaFontPool } from "./api";
import type { DaFontPoolResponse } from "./api";
import "./DaFontPoolDialog.css";

export function DaFontPoolDialog({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState<DaFontPoolResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void getStage1DaFontPool(controller.signal)
      .then(data => { if (!controller.signal.aborted) setValue(data); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, []);
  return createPortal(
    <div className="rrugc-dafont-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="rrugc-dafont-dialog" role="dialog" aria-modal="true" aria-label="Stage 1 DaFont font pool">
        <header>
          <div>
            <span className="rrugc-dafont-eyebrow">GATORHATS PRODUCTS · STAGE 1</span>
            <h2>DaFont Pool</h2>
            <p>Licensed fonts · six unique fonts for every job · brown embroidery only.</p>
          </div>
          <button type="button" className="rrugc-dafont-close" onClick={onClose} aria-label="Close DaFont Pool">×</button>
        </header>
        {error && <p role="alert" className="rrugc-dafont-error">{error}</p>}
        {!value && !error && <p className="rrugc-dafont-loading">Checking installed font registry…</p>}
        {value && <>
          <div className="rrugc-dafont-metrics">
            <div><strong>{value.approved_fonts}</strong><span>Approved fonts</span></div>
            <div><strong>{value.fonts_per_job}</strong><span>Unique per job</span></div>
            <div><strong>{value.avoid_recent_jobs}</strong><span>Recent jobs avoided</span></div>
            <div><strong>{value.lookback_jobs}</strong><span>Jobs in history</span></div>
          </div>
          <p className="rrugc-dafont-policy">Every new generation chooses six different DaFont families. The pool avoids the previous {value.avoid_recent_jobs} jobs when capacity allows and penalizes repeated fonts over the last {value.lookback_jobs}. A retry retains its original fonts; Regenerate selects a new set. Every design stays typography-only and brown-thread-only.</p>
          <div className="rrugc-dafont-font-list" role="list">
            {value.fonts.map(font => <div key={font.name} className="rrugc-dafont-font-item" role="listitem">
              <div><strong>{font.name}</strong><small>{font.category} · {font.group}</small></div>
              <span title="Uses among the 30 most recent font-planned jobs">{font.times_used_recently} recent</span>
              <a href={font.url} target="_blank" rel="noreferrer">DaFont ↗</a>
              <small>{font.license}</small>
            </div>)}
          </div>
        </>}
        <footer><button type="button" onClick={onClose}>Close</button></footer>
      </section>
    </div>,
    document.body,
  );
}
