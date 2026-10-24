import { useEffect, useRef, useState } from "react";
import {
  applyHatStockZip, previewHatStockZip,
  type HatStockPreview,
} from "./api";
import "./HatStockSyncModal.css";

const SIZE_LIMIT = 200 * 1024 * 1024;

export function HatStockSyncModal({ onClose, onApplied }: {
  onClose: () => void;
  onApplied: () => void | Promise<void>;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<HatStockPreview | null>(null);
  const [result, setResult] = useState<HatStockPreview | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.querySelector<HTMLInputElement>('input[type="file"]')?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),a[href]') || [])];
      if (!focusable.length) { event.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      document.body.style.overflow = oldOverflow;
      previousFocus?.focus();
    };
  }, []);

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [busy, onClose]);

  async function inspect() {
    if (!file || busy) return;
    setBusy("preview");
    setError("");
    setResult(null);
    try {
      if (!file.name.toLowerCase().endsWith(".zip") || file.size > SIZE_LIMIT || !file.size) {
        throw new Error("Choose an image ZIP smaller than 200 MB.");
      }
      setPreview(await previewHatStockZip(file));
    } catch (reason) {
      setPreview(null);
      setError(reason instanceof Error ? reason.message : "Unable to inspect ZIP.");
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!file || !preview?.ready_count || busy) return;
    setBusy("apply");
    setError("");
    try {
      const applied = await applyHatStockZip(file, preview.sha256);
      setResult(applied);
      try { await onApplied(); } catch {
        // Import is already committed; a refresh error must not mask success.
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sync stock images.");
    } finally {
      setBusy(null);
    }
  }

  return <div className="rrugc-hat-sync-backdrop" role="presentation"
    onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section ref={dialogRef} className="rrugc-hat-sync-modal" role="dialog" aria-modal="true"
      aria-labelledby="rrugc-hat-sync-title">
      <header className="rrugc-hat-sync-header">
        <div>
          <span>GATORHATS · IMAGE LIBRARY</span>
          <h2 id="rrugc-hat-sync-title">Sync Hat Images</h2>
          <p>Match all cap views by color name in the ZIP. Incomplete and duplicate color sets are excluded automatically.</p>
        </div>
        <button type="button" className="rrugc-hat-sync-close" onClick={onClose} disabled={!!busy} aria-label="Close image sync">×</button>
      </header>
      <div className="rrugc-hat-sync-body">
        <div className="rrugc-hat-sync-upload">
          <label htmlFor="rrugc-hat-sync-file">Select ZIP containing the official hat photographs</label>
          <input id="rrugc-hat-sync-file" type="file" accept=".zip,application/zip" disabled={!!busy}
            onChange={event => {
              setFile(event.target.files?.[0] || null);
              setPreview(null); setResult(null); setError("");
            }} />
          <small>Recognizes Front, Side, Back and optional 3/4 views in file or folder names. Maximum 200 MB.</small>
          <button className="rrugc-primary" type="button" disabled={!file || !!busy} onClick={() => void inspect()}>
            {busy === "preview" ? "Scanning ZIP…" : "Preview groups"}
          </button>
        </div>
        {error && <p role="alert" className="rrugc-hat-sync-error">{error}</p>}
        {preview && <>
          <div className="rrugc-hat-sync-summary" role="status">
            <strong>{preview.ready_count} ready groups</strong>
            <span>{preview.groups.length} colors detected</span>
            <span>{preview.file_count} photographs</span>
            {!!preview.unmatched.length && <span>{preview.unmatched.length} unmatched</span>}
          </div>
          <div className="rrugc-hat-sync-list" aria-label="Preview grouped hat photographs">
            {preview.groups.map(group => <article key={group.color} className={group.ready ? "is-ready" : "is-incomplete"}>
              <div className="rrugc-hat-sync-group-header">
                <strong>{group.color.replaceAll("-", " ")}</strong>
                <span>{group.ready ? "Ready" : group.duplicates.length ? "Duplicate views" : "Missing views"}</span>
              </div>
              <div className="rrugc-hat-sync-views">
                {(["front", "side", "back", "three-quarter"] as const).map(view =>
                  <div key={view} className={group.views[view] ? "is-present" : "is-missing"}>
                    <b>{view === "three-quarter" ? "3/4" : view}</b>
                    <small title={(group.views[view] || []).join(", ")}>
                      {(group.views[view] || []).map(item => item.split("/").pop()).join(", ") || "—"}
                    </small>
                  </div>)}
              </div>
              {!!group.missing.length && <small>Missing: {group.missing.join(", ")}</small>}
              {!!group.duplicates.length && <small>Duplicates: {group.duplicates.join(", ")}</small>}
            </article>)}
          </div>
          {!!preview.unmatched.length && <details className="rrugc-hat-sync-unmatched">
            <summary>{preview.unmatched.length} unmatched or invalid files</summary>
            <ul>{preview.unmatched.slice(0, 30).map((item, index) => <li key={index}>{item.file} · {item.error}</li>)}</ul>
          </details>}
          <p className="rrugc-hat-sync-safe-note">
            Only complete groups will be imported. Existing outputs and completed AI jobs remain unchanged.
          </p>
        </>}
        {result && <p role="status" className="rrugc-hat-sync-success">
          Synced {result.applied_count} color groups: {result.applied?.join(", ")}. New references are active for future runs.
        </p>}
      </div>
      <footer className="rrugc-hat-sync-footer">
        <button type="button" onClick={onClose} disabled={!!busy}>Close</button>
        <button type="button" className="rrugc-primary" disabled={!preview?.ready_count || !!busy || !!result}
          onClick={() => void apply()}>
          {busy === "apply" ? "Syncing images…" : "Sync ready color groups"}
        </button>
      </footer>
    </section>
  </div>;
}
