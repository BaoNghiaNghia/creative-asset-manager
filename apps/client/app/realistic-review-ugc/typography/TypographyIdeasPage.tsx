import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { RrugcApiError } from "../api";
import { DeferredImage } from "../DeferredImage";
import { StyleDnaPanel } from "./StyleDnaPanel";
import {
  archiveTypographyReference, getTypographyReference,
  listTypographyReferences, patchTypographyReference, uploadTypographyReferences,
  voteTypographyReference, reconcileTypographyFeedback,
} from "./api";
import type {
  TypographyEditorialStatus, TypographyPrivacy, TypographyReference,
  TypographyReferenceFilters, TypographyReferencePage, TypographyRights, TypographyScope,
  TypographySourceType, TypographyStyleRole,
} from "./types";
import "./TypographyIdeasPage.css";

const BATCH_LIMIT = 20;
const MAX_FILE_BYTES = 20 * 1024 * 1024;
const FORMAT = new Set(["image/jpeg", "image/png", "image/webp"]);
const PAGE_SIZE = 80;
const RIGHTS: TypographyRights[] = ["unknown", "owned", "licensed", "permitted_reference", "blocked"];
const RIGHTS_VERIFIED = new Set<TypographyRights>(["owned", "licensed", "permitted_reference"]);
const STATUS: TypographyEditorialStatus[] = ["candidate", "approved", "rejected", "archived"];
type UploadState = "queued" | "uploading" | "created" | "duplicate" | "error";
type UploadItem = { id: number; file: File; state: UploadState; error?: string };
let nextFileId = 0;

function feedback(error: unknown): string {
  if (error instanceof RrugcApiError) return error.code ? `${error.message} (${error.code})` : error.message;
  return error instanceof Error ? error.message : "The operation could not be completed.";
}

function useDialogKeyboard(onClose: () => void) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const section = ref.current;
    section?.querySelector<HTMLElement>('button[aria-label^="Close"]')?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== "Tab" || !ref.current) return;
      const controls = [...ref.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]',
      )].filter(node => node.getClientRects().length > 0);
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); before?.focus(); };
  }, []);
  return ref;
}

function displayTitle(ref: TypographyReference) {
  const name = ref.filename || ref.source_attribution || "Typography reference";
  return name.replace(/\.(png|jpe?g|webp)$/i, "");
}

function compactTitle(ref: TypographyReference) {
  const title = displayTitle(ref);
  const pin = title.match(/^typography-pin-(\d+)$/i);
  return pin ? "Pin #" + pin[1].slice(-8) : title;
}

function imageThumbnailUrl(url: string): string {
  return url + (url.includes("?") ? "&" : "?") + "thumbnail=true&size=320";
}

function statusLabel(ref: TypographyReference) {
  if (ref.preference === "positive") return "Picked";
  if (ref.preference === "negative") return "Rejected";
  if (ref.editorial_status === "archived") return "Archived";
  if (ref.editorial_status === "rejected") return "Rejected";
  if (ref.analysis_status === "failed") return "Analysis failed";
  if (ref.analysis_status === "running") return "Analyzing";
  if (ref.editorial_status === "approved") return "Approved";
  return "Needs review";
}

export function TypographyIdeasPage({ active, firstPage, onCountChange }: {
  active: boolean;
  firstPage: TypographyReferencePage;
  onCountChange: (count: number) => void;
}) {
  const [page, setPage] = useState<TypographyReferencePage>(firstPage);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [status, setStatus] = useState<TypographyEditorialStatus | "">("");
  const [sourceType, setSourceType] = useState<TypographySourceType | "">("");
  const [scope, setScope] = useState<TypographyScope | "">("");
  const [styleRole, setStyleRole] = useState<TypographyStyleRole | "">("");
  const [sort, setSort] = useState<"newest" | "oldest" | "priority">("newest");
  const [myUploadsOnly, setMyUploadsOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailRow, setDetailRow] = useState<TypographyReference | null>(null);
  const [voteBusy, setVoteBusy] = useState<Set<string>>(new Set());
  const votingRef = useRef(new Set<string>());
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const requestedReconcile = useRef(false);
  useEffect(() => () => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { setDebouncedSearch(search.trim()); setOffset(0); }, 275);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (!active) return;
    const ctrl = new AbortController();
    const filters: TypographyReferenceFilters = {
      query: debouncedSearch, status, sourceType, scope, styleRole, sort, myUploadsOnly, offset, limit: PAGE_SIZE,
    };
    setLoading(true); setError("");
    void listTypographyReferences(filters, ctrl.signal).then(result => {
      if (ctrl.signal.aborted) return;
      setPage(result);
      onCountChange(result.stats.total);
    }).catch(reason => {
      if (!ctrl.signal.aborted) setError(feedback(reason));
    }).finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [active, debouncedSearch, status, sourceType, scope, styleRole, sort, myUploadsOnly, offset, revision, onCountChange]);

  useEffect(() => {
    if (!active) {
      requestedReconcile.current = false;
      return;
    }
    if (!page.capabilities.manage || requestedReconcile.current) return;
    requestedReconcile.current = true;
    let alive = true;
    let rounds = 0;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const catchUp = async () => {
      try {
        const result = await reconcileTypographyFeedback();
        if (!alive) return;
        if (result.legacy_imported > 0) {
          setNotice(`Imported ${result.legacy_imported} earlier approved ideas into auto-learning.`);
        }
        if (result.legacy_imported || result.queued) {
          setRevision(value => value + 1);
          timers.push(window.setTimeout(() => {
            if (alive) setRevision(value => value + 1);
          }, 5000));
        }
        if (result.remaining > 0 && ++rounds < 4) {
          timers.push(window.setTimeout(() => { if (alive) void catchUp(); }, 12000));
        }
      } catch (cause) {
        if (alive) setError(feedback(cause));
      }
    };
    void catchUp();
    return () => {
      alive = false;
      timers.forEach(timer => window.clearTimeout(timer));
    };
  }, [active, page.capabilities.manage]);

  const vote = async (ref: TypographyReference, choice: "picked" | "rejected") => {
    if (votingRef.current.has(ref.id) || !page.capabilities.manage) return;
    const decision = ref.preference === (choice === "picked" ? "positive" : "negative")
      ? "clear" : choice;
    votingRef.current.add(ref.id);
    setVoteBusy(new Set(votingRef.current));
    setError("");
    try {
      const result = await voteTypographyReference(ref.id, decision);
      setPage(current => ({
        ...current,
        items: current.items.map(item => item.id === ref.id ? result.reference : item),
      }));
      if (detailId === ref.id) setDetailRow(result.reference);
      setNotice(decision === "clear" ? "Preference cleared. Future designs will use the latest votes."
        : choice === "picked" ? "Picked. CAM is learning the style automatically."
        : "Rejected. CAM will learn what to avoid.");
      // Coalesce multiple fast votes into one aggregate refresh.
      if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(() => setRevision(value => value + 1), 750);
    } catch (cause) {
      setError(feedback(cause));
    } finally {
      votingRef.current.delete(ref.id);
      setVoteBusy(new Set(votingRef.current));
    }
  };

  const selected = useMemo(
    // Prefer the acknowledged mutation while list refresh is in progress.
    () => detailId ? (detailRow?.id === detailId ? detailRow : page.items.find(row => row.id === detailId) || null) : null,
    [page.items, detailId, detailRow],
  );

  const refresh = () => setRevision(value => value + 1);
  const reset = () => {
    setSearch(""); setStatus(""); setSourceType(""); setScope(""); setStyleRole(""); setSort("newest");
    setMyUploadsOnly(false); setOffset(0);
  };
  const onUpdate = (row: TypographyReference) => {
    setDetailRow(row); setNotice("Reference updated."); refresh();
  };
  const onArchive = () => {
    setDetailId(null); setDetailRow(null); setNotice("Reference archived; its image has not been deleted."); refresh();
  };
  return <div className="typography-ideas" data-testid="typography-ideas" aria-busy={loading}>
    <div className="typography-ideas-heading">
      <div className="typography-ideas-copy">
        <p className="typography-eyebrow">STAGE 1 · AUTOMATIC STYLE LEARNING</p>
        <h3>Typography ideas</h3>
        <p>One-click feedback for better typography. Pick what works, reject what does not; CAM learns for the next batch.</p>
      </div>
      {page.capabilities.upload && <button type="button" className="typography-primary" onClick={() => setUploadOpen(true)} data-testid="typography-upload-open">
        <span aria-hidden="true">＋</span> Upload ideas
      </button>}
    </div>
    <div className="typography-ideas-counters" aria-label="Typography learning summary">
      <span><strong>{page.stats.total}</strong> References</span>
      <span><strong>{page.stats.picked ?? page.stats.approved}</strong> Picked</span>
      <span><strong>{page.stats.rejected ?? 0}</strong> Rejected</span>
      <span><strong>{page.stats.learned ?? 0}</strong> Patterns learned</span>
    </div>
    <div className="typography-auto-learning" role="status" aria-label="Automatic typography learning">
      <span className="typography-learning-dot" aria-hidden="true" />
      <div><strong>Auto-learning is on</strong>
        <span>Pick or reject a reference. CAM saves your preference, analyzes visual patterns locally and uses aggregated feedback in the next Generate / Regenerate batch.</span></div>
      <small>Auto-recovery · No pilot · No paid AI calls per vote</small>
    </div>
    <div className="typography-ideas-filterbar" role="search" aria-label="Filter typography ideas">
      <label className="typography-filter-search"><span className="sr-only">Search typography references</span>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search style, notes or source…" />
      </label>
      <label><span className="sr-only">Style component</span><select value={styleRole}
        onChange={e => { setStyleRole(e.target.value as TypographyStyleRole | ""); setOffset(0); }} aria-label="Typography style component">
        <option value="">All styles</option><option value="layout">Layout</option><option value="font_mood">Font</option><option value="overlap">Overlap</option>
      </select></label>
      <label><span className="sr-only">Status</span><select value={status} onChange={e => { setStatus(e.target.value as typeof status); setOffset(0); }} aria-label="Typography approval status">
        <option value="">All decisions</option>{STATUS.map(value => <option key={value} value={value}>{value === "candidate" ? "Unreviewed" : value === "approved" ? "Picked" : value}</option>)}
      </select></label>
      {showMoreFilters && <>
      <label><span className="sr-only">Source</span><select value={sourceType} onChange={e => { setSourceType(e.target.value as typeof sourceType); setOffset(0); }} aria-label="Typography source">
        <option value="">All sources</option><option value="upload">My / team uploads</option><option value="scout">Scout</option><option value="internal">Internal</option>
      </select></label>
      <label><span className="sr-only">Scope</span><select value={scope} onChange={e => { setScope(e.target.value as typeof scope); setOffset(0); }} aria-label="Typography scope">
        <option value="">All scopes</option><option value="workspace">Workspace</option><option value="campaign">Campaign</option><option value="keyword">Keyword</option><option value="skill">Skill</option>
      </select></label>
      </>}
      <label><span className="sr-only">Sort</span><select value={sort} onChange={e => { setSort(e.target.value as typeof sort); setOffset(0); }} aria-label="Sort typography ideas">
        <option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="priority">Priority</option>
      </select></label>
      {showMoreFilters && <label className="typography-toggle"><input type="checkbox" checked={myUploadsOnly} onChange={e => { setMyUploadsOnly(e.target.checked); setOffset(0); }} /> My uploads</label>}
      <button type="button" className="typography-more-filters" aria-expanded={showMoreFilters}
        onClick={() => setShowMoreFilters(value => !value)}>{showMoreFilters ? "Less" : "More filters"}</button>
      <button type="button" className="typography-quiet" onClick={reset}>Reset</button>
    </div>
    {notice && <div className="typography-notice" role="status">{notice}<button type="button" onClick={() => setNotice("")} aria-label="Dismiss message">×</button></div>}
    {error && <div className="typography-error" role="alert"><span>{error}</span><button type="button" onClick={refresh}>Retry</button></div>}
    {loading && !page.items.length ? <div className="typography-empty" role="status">Loading typography references…</div>
      : page.items.length ? <div className="typography-ideas-grid" aria-label="Typography image references">
        {page.items.map(ref => <article key={ref.id} className="typography-reference-card" data-testid="typography-reference-card">
          <button className="typography-reference-image" type="button" onClick={() => { setDetailId(ref.id); setDetailRow(ref); }}
            aria-label={"View typography reference " + displayTitle(ref)}>
            <DeferredImage src={imageThumbnailUrl(ref.image_url)} rootMargin="120px 0px"
              embeddedInButton alt={displayTitle(ref)} loading="lazy" decoding="async" />
            <span className={"typography-status-pill typography-status-" + (ref.preference === "positive" ? "approved" : ref.preference === "negative" ? "rejected" : ref.editorial_status)}>{statusLabel(ref)}</span>
          </button>
          <div className="typography-reference-info">
            <strong title={displayTitle(ref)}>{compactTitle(ref)}</strong>
            <div className="typography-reference-actions" role="group" aria-label={"Style preference for " + compactTitle(ref)}>
              {page.capabilities.manage && <>
                <button type="button" className={"typography-vote typography-vote-pick" + (ref.preference === "positive" ? " is-selected" : "")}
                  data-testid="typography-pick" aria-pressed={ref.preference === "positive"}
                  aria-label={"Pick " + compactTitle(ref)} title="Pick · teach CAM to favor this style"
                  disabled={voteBusy.has(ref.id) || ref.editorial_status === "archived"}
                  onClick={() => void vote(ref, "picked")}>✓</button>
                <button type="button" className={"typography-vote typography-vote-reject" + (ref.preference === "negative" ? " is-selected" : "")}
                  data-testid="typography-reject" aria-pressed={ref.preference === "negative"}
                  aria-label={"Reject " + compactTitle(ref)} title="Reject · teach CAM to avoid this style"
                  disabled={voteBusy.has(ref.id) || ref.editorial_status === "archived"}
                  onClick={() => void vote(ref, "rejected")}>×</button>
              </>}
              <button type="button" className="typography-reference-details" aria-label={"View details for " + compactTitle(ref)}
                onClick={() => { setDetailId(ref.id); setDetailRow(ref); }}>Details</button>
            </div>
            <span className="typography-reference-source">{ref.source_type} · {
              ref.preference === "neutral" ? "Awaiting vote" :
              ref.feedback_learning_state === "ready" ? "Pattern learned" :
              ref.feedback_learning_state === "excluded" ? "Excluded from learning" :
              "Learning pending"
            }</span>
          </div>
        </article>)}
      </div> : !loading && !error ? <div className="typography-empty">
        <strong>No typography references yet</strong><p>Upload artwork ideas or clear your filters. Existing keyword designs are unchanged.</p>
        {page.capabilities.upload && <button type="button" className="typography-primary" onClick={() => setUploadOpen(true)}>Upload ideas</button>}
      </div> : null}
    <footer className="typography-pagination">
      <span>{page.total ? page.offset + 1 : 0}–{Math.min(page.offset + page.limit, page.total)} of {page.total} results</span>
      <div>
        <button type="button" disabled={loading || offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
        <button type="button" disabled={loading || offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
      </div>
    </footer>
    {uploadOpen && <TypographyUploadDialog onClose={() => setUploadOpen(false)} onUpdated={() => { refresh(); setNotice("Upload finished. Review the status of each image below."); }} />}
    {selected && detailId && <TypographyDetailDrawer reference={selected} canManage={page.capabilities.manage}
      onClose={() => { setDetailId(null); setDetailRow(null); }} onUpdate={onUpdate} onArchive={onArchive}
      onVote={vote} />}
  </div>;
}

function TypographyUploadDialog({ onClose, onUpdated }: { onClose: () => void; onUpdated: () => void }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [rights, setRights] = useState<TypographyRights>("unknown");
  const [privacy, setPrivacy] = useState<TypographyPrivacy>("private");
  const [sourceAttribution, setSourceAttribution] = useState("");
  const [uploadRightsConfirmed, setUploadRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const dialogRef = useDialogKeyboard(() => { if (!busy) onClose(); });
  const inputRef = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const updateItem = (id: number, state: UploadState, message?: string) =>
    setItems(current => current.map(item => item.id === id ? { ...item, state, error: message } : item));

  useEffect(() => () => controllerRef.current?.abort(), []);
  const addFiles = (files: FileList | File[]) => {
    const selected = Array.from(files);
    setItems(previous => {
      const remaining = Math.max(0, BATCH_LIMIT - previous.length);
      return [...previous, ...selected.slice(0, remaining).map(file => ({
        id: ++nextFileId, file,
        state: FORMAT.has(file.type) && file.size > 0 && file.size <= MAX_FILE_BYTES ? "queued" as const : "error" as const,
        error: !FORMAT.has(file.type) ? "Only PNG, JPEG and WebP are supported." :
          !file.size ? "File is empty." : file.size > MAX_FILE_BYTES ? "Exceeds 20 MB." : undefined,
      }))];
    });
    if (selected.length + items.length > BATCH_LIMIT) setError("Upload up to 20 files per batch.");
  };
  const submit = async (specific?: UploadItem) => {
    const pending = specific ? [specific] : items.filter(item => item.state === "queued");
    if (!pending.length || busy) return;
    if (RIGHTS_VERIFIED.has(rights) && (!uploadRightsConfirmed || !sourceAttribution.trim())) {
      setError("Verify the source rights, add attribution and confirm the review before upload.");
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true); setError("");
    // Bound memory on the VPS: at most three simultaneous multipart requests,
    // with no more than two 20 MB images in each request. Never flood all chunks.
    const chunks: UploadItem[][] = [];
    for (let index = 0; index < pending.length; index += 2) chunks.push(pending.slice(index, index + 2));
    let nextChunk = 0;
    let succeeded = false;
    const worker = async () => {
      while (nextChunk < chunks.length && !controller.signal.aborted) {
        const chunk = chunks[nextChunk++];
        for (const item of chunk) updateItem(item.id, "uploading");
        try {
          const result = await uploadTypographyReferences(chunk.map(item => item.file),
            { rights, privacy, sourceAttribution, rightsReviewConfirmed: uploadRightsConfirmed }, controller.signal);
          result.items.forEach(item => {
            const target = chunk[item.index];
            if (target) {
              updateItem(target.id, item.status, item.code);
              if (item.status !== "error") succeeded = true;
            }
          });
        } catch (reason) {
          if (!controller.signal.aborted) {
            for (const item of chunk) updateItem(item.id, "error", feedback(reason));
          }
        }
      }
    };
    try {
      await Promise.all(Array.from({ length: Math.min(3, chunks.length) }, () => worker()));
      if (succeeded) onUpdated();
    } finally {
      controllerRef.current = null;
      setBusy(false);
    }
  };
  return createPortal(<div className="typography-dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="typography-upload-dialog" ref={dialogRef as React.RefObject<HTMLElement>} role="dialog"
      aria-modal="true" aria-label="Upload typography ideas" data-testid="typography-upload-dialog">
      <header><div><small>TYPOGRAPHY IDEAS / REFERENCE LIBRARY</small><h2>Upload ideas</h2>
        <p>Images are saved privately. Pick or reject them in the grid to teach CAM automatically.</p></div>
        <button type="button" className="typography-close" aria-label="Close upload" disabled={busy} onClick={onClose}>×</button></header>
      <div className={"typography-dropzone" + (dragging ? " is-dragging" : "")}
        onDragOver={event => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) addFiles(event.dataTransfer.files); }}>
        <strong>Drop typography images here</strong><span>PNG, JPEG or WebP · 20 MB each · up to 20 files</span>
        <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" multiple
          aria-label="Choose typography reference images" onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = ""; }} hidden />
        <button type="button" disabled={busy || items.length >= BATCH_LIMIT} onClick={() => inputRef.current?.click()}>Choose images</button>
      </div>
      <details className="typography-upload-advanced">
        <summary>Advanced source settings (optional)</summary>
      <div className="typography-upload-settings">
        <label>Source rights<select value={rights} disabled={busy} onChange={event => setRights(event.target.value as TypographyRights)}>
          {RIGHTS.map(value => <option key={value} value={value}>{value === "permitted_reference" ? "Permitted reference" : value}</option>)}
        </select></label>
        <label>Visibility<select value={privacy} disabled={busy} onChange={event => setPrivacy(event.target.value as TypographyPrivacy)}>
          <option value="private">Private (default)</option><option value="workspace">Workspace</option>
        </select></label>
        <label>Source / attribution<input value={sourceAttribution} disabled={busy} maxLength={400}
          onChange={event => setSourceAttribution(event.target.value)} placeholder="Optional source or author…" /></label>
      </div>
      {RIGHTS_VERIFIED.has(rights) && <div className="typography-rights-verification" role="group" aria-label="Upload rights verification">
        <strong>Source rights must be independently verified</strong>
        <p>A source URL alone is not proof of a license or ownership. Provide the original source or permission reference.</p>
        <label><input type="checkbox" checked={uploadRightsConfirmed} onChange={event => setUploadRightsConfirmed(event.target.checked)} /> I reviewed the supporting rights evidence.</label>
      </div>}
      <p className="typography-rights-notice">Uploading does not verify copyright or grant rights to copy. The default private reference can still teach generalized styles.</p>
      </details>
      <div className="typography-upload-list" aria-live="polite">
        {items.map(item => <div className="typography-upload-file" key={item.id}>
          <span title={item.file.name}>{item.file.name}</span><small>{(item.file.size / (1024 * 1024)).toFixed(1)} MB</small>
          <b className={"state-" + item.state}>{item.state === "duplicate" ? "Already in library" : item.state}{item.error ? " · " + item.error : ""}</b>
          {item.state === "error" && FORMAT.has(item.file.type) && item.file.size > 0 && item.file.size <= MAX_FILE_BYTES
            && <button type="button" disabled={busy} onClick={() => void submit(item)}>Retry</button>}
          {!busy && item.state === "queued" && <button type="button" aria-label={"Remove " + item.file.name}
            onClick={() => setItems(current => current.filter(next => next.id !== item.id))}>×</button>}
        </div>)}
      </div>
      {error && <p className="typography-error" role="alert">{error}</p>}
      <footer><button type="button" className="typography-quiet" disabled={busy} onClick={onClose}>Close</button>
        <button type="button" className="typography-primary" disabled={busy || !items.some(item => item.state === "queued") || (RIGHTS_VERIFIED.has(rights) && (!uploadRightsConfirmed || !sourceAttribution.trim()))}
          onClick={() => void submit()}>{busy ? "Uploading…" : "Upload " + items.filter(item => item.state === "queued").length + " images"}</button>
      </footer>
    </section>
  </div>, document.body);
}

function TypographyDetailDrawer({ reference, canManage, onClose, onUpdate, onArchive, onVote }: {
  reference: TypographyReference; canManage: boolean;
  onClose: () => void; onUpdate: (next: TypographyReference) => void; onArchive: () => void;
  onVote: (ref: TypographyReference, decision: "picked" | "rejected") => Promise<void>;
}) {
  const [tab, setTab] = useState<"overview" | "dna" | "usage">("overview");
  const [editing, setEditing] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState(false);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const archiveCancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useDialogKeyboard(() => {
    if (archiveConfirm) setArchiveConfirm(false);
    else if (!busy) onClose();
  });
  useEffect(() => {
    if (archiveConfirm) archiveCancelRef.current?.focus();
  }, [archiveConfirm]);
  const [draft, setDraft] = useState({
    privacy: reference.privacy,
    source_rights_status: reference.source_rights_status,
    scope: reference.scope,
    campaign_id: reference.campaign_id || "",
    keyword_id: reference.keyword_id || "",
    skill_name: reference.skill_name || "",
    preference: reference.preference,
    tags: reference.tags.join(", "),
    notes: reference.notes || "",
    source_attribution: reference.source_attribution || "",
    priority: reference.priority,
    reference_roles: reference.reference_roles,
  });
  useEffect(() => {
    if (editing) return;
    setDraft({
      privacy: reference.privacy,
      source_rights_status: reference.source_rights_status,
      scope: reference.scope,
      campaign_id: reference.campaign_id || "",
      keyword_id: reference.keyword_id || "",
      skill_name: reference.skill_name || "",
      preference: reference.preference,
      tags: reference.tags.join(", "),
      notes: reference.notes || "",
      source_attribution: reference.source_attribution || "",
      priority: reference.priority,
      reference_roles: reference.reference_roles,
    });
  }, [reference, editing]);
  const verifyTransition = RIGHTS_VERIFIED.has(draft.source_rights_status) &&
    !RIGHTS_VERIFIED.has(reference.source_rights_status);
  const rightsEvidenceMissing = verifyTransition && (!rightsConfirmed || !draft.source_attribution.trim());
  const save = async () => {
    if (busy) return;
    if (rightsEvidenceMissing) {
      setError("Record the source or license attribution and confirm the rights review before saving.");
      return;
    }
    setBusy(true); setError("");
    try {
      const data = await patchTypographyReference(reference.id, {
        privacy: draft.privacy,
        source_rights_status: draft.source_rights_status,
        scope: draft.scope,
        campaign_id: draft.scope === "campaign" ? draft.campaign_id : null,
        keyword_id: draft.scope === "keyword" ? draft.keyword_id : null,
        skill_name: draft.scope === "skill" ? draft.skill_name : null,
        preference: draft.preference,
        priority: draft.priority,
        tags: draft.tags.split(",").map(v => v.trim()).filter(Boolean).slice(0, 32),
        notes: draft.notes, source_attribution: draft.source_attribution,
        ...(verifyTransition ? { rights_review_confirmed: rightsConfirmed } : {}),
        reference_roles: draft.reference_roles,
      });
      onUpdate(data); setEditing(false); setRightsConfirmed(false);
    } catch (reason) { setError(feedback(reason)); }
    finally { setBusy(false); }
  };
  const archive = async () => {
    setBusy(true); setError("");
    try { await archiveTypographyReference(reference.id); onArchive(); }
    catch (reason) { setError(feedback(reason)); setBusy(false); setArchiveConfirm(false); }
  };
  return createPortal(<div className="typography-dialog-backdrop typography-drawer-backdrop"
      onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <section className="typography-detail-drawer" ref={dialogRef as React.RefObject<HTMLElement>} role="dialog" aria-modal="true"
      aria-label={"Typography reference " + displayTitle(reference)} data-testid="typography-detail-drawer">
      <header><div><small>REFERENCE / {reference.influence.toUpperCase()}</small><h2 title={displayTitle(reference)}>{compactTitle(reference)}</h2>
        <p>{reference.editorial_status} · {reference.analysis_status} · {reference.privacy}</p></div>
        <button type="button" className="typography-close" aria-label="Close details" onClick={onClose}>×</button></header>
      <div className="typography-drawer-body">
        <div className="typography-drawer-image"><img src={reference.image_url} alt={displayTitle(reference)} /></div>
        <div className="typography-drawer-tabs" role="tablist" aria-label="Reference details">
          {(["overview", "dna", "usage"] as const).map(value => <button type="button" role="tab"
            key={value} aria-selected={tab === value} onClick={() => setTab(value)}>
            {value === "overview" ? "Overview" : value === "dna" ? "Style DNA" : "Usage & lineage"}
          </button>)}
        </div>
        {tab === "overview" && <div className="typography-drawer-content">
          {editing && canManage ? <>
            <div className="typography-editor-fields">
              <label>Source rights<select value={draft.source_rights_status} onChange={e => setDraft(prev => ({ ...prev, source_rights_status: e.target.value as TypographyRights }))}>
                {RIGHTS.map(value => <option key={value} value={value}>{value}</option>)}
              </select></label>
              <label>Visibility<select value={draft.privacy} onChange={e => setDraft(prev => ({ ...prev, privacy: e.target.value as TypographyPrivacy }))}>
                <option value="private">Private</option><option value="workspace">Workspace</option>
              </select></label>
              <label>Reference scope<select value={draft.scope} onChange={e => setDraft(prev => ({
                ...prev, scope: e.target.value as TypographyScope,
                campaign_id: "", keyword_id: "", skill_name: "",
              }))}>
                <option value="workspace">Workspace</option>
                <option value="campaign">Campaign</option>
                <option value="keyword">Keyword</option>
                <option value="skill">Skill</option>
              </select></label>
              {draft.scope === "campaign" && <label>Campaign ID<input value={draft.campaign_id}
                maxLength={36} onChange={e => setDraft(prev => ({ ...prev, campaign_id: e.target.value }))}
                placeholder="ID within current tenant" /></label>}
              {draft.scope === "keyword" && <label>Keyword ID<input value={draft.keyword_id}
                maxLength={36} onChange={e => setDraft(prev => ({ ...prev, keyword_id: e.target.value }))}
                placeholder="Stage 0 keyword ID" /></label>}
              {draft.scope === "skill" && <label>Skill name<input value={draft.skill_name}
                maxLength={128} onChange={e => setDraft(prev => ({ ...prev, skill_name: e.target.value }))}
                placeholder="e.g. concept-short-v2" /></label>}
              <label>Priority<input type="number" min={-100} max={100} value={draft.priority} onChange={e => setDraft(prev => ({ ...prev, priority: Number(e.target.value) }))} /></label>
              <label>Tags<input value={draft.tags} placeholder="Serif, Script, Interlock…" onChange={e => setDraft(prev => ({ ...prev, tags: e.target.value }))} /></label>
              <label>Attribution / license source<input value={draft.source_attribution} maxLength={400} onChange={e => setDraft(prev => ({ ...prev, source_attribution: e.target.value }))} placeholder="Owner, license reference or permission record" /></label>
              {verifyTransition && <div className="typography-rights-verification" role="group" aria-label="Rights verification checklist">
                <strong>Verify source rights for G3</strong>
                <p>Check the original source and written ownership, license or permission record. A Pinterest or Scout URL alone does not establish rights. Do not confirm without evidence.</p>
                <label><input type="checkbox" checked={rightsConfirmed} onChange={event => setRightsConfirmed(event.target.checked)} /> I reviewed the original source and can substantiate this rights status.</label>
                {!draft.source_attribution.trim() && <small>Source attribution or permission reference is required to save verified rights.</small>}
              </div>}
              <label>Notes<textarea value={draft.notes} maxLength={2000} onChange={e => setDraft(prev => ({ ...prev, notes: e.target.value }))} /></label>
              <fieldset><legend>Style components</legend>
                {(["layout", "font_mood", "overlap"] as const).map(role => <label key={role}><input type="checkbox"
                  checked={draft.reference_roles.includes(role)} onChange={e => setDraft(prev => ({
                    ...prev, reference_roles: e.target.checked
                      ? [...prev.reference_roles, role] : prev.reference_roles.filter(value => value !== role),
                  }))} />{role === "font_mood" ? "Font" : role}</label>)}
              </fieldset>
            </div>
            <div className="typography-editor-actions"><button type="button" disabled={busy} onClick={() => { setEditing(false); setRightsConfirmed(false); }}>Cancel</button>
              <button type="button" disabled={busy || !draft.reference_roles.length || rightsEvidenceMissing} className="typography-primary"
                onClick={() => void save()}>{busy ? "Saving…" : "Save changes"}</button>
            </div>
          </> : <>
            <dl className="typography-info-grid">
              <div><dt>Rights</dt><dd>{reference.source_rights_status}</dd></div>
              <div><dt>Scope</dt><dd>{reference.scope}</dd></div>
              <div><dt>Visibility</dt><dd>{reference.privacy}</dd></div>
              <div><dt>Source</dt><dd>{reference.source_type}</dd></div>
              <div><dt>Approval</dt><dd>{reference.editorial_status}</dd></div>
              <div><dt>Style mode</dt><dd>Balanced</dd></div>
            </dl>
            <p>{reference.notes || "No notes added yet."}</p>
            <div className="typography-reference-tags">{reference.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
            <p className="typography-learning-note">CAM learns your Pick / Reject preferences for future batches. Style features are approximate and are never copied from this source image.</p>
          </>}
        </div>}
        {tab === "dna" && <div className="typography-drawer-content">
          <StyleDnaPanel reference={reference} canManage={canManage}
            onVerifyRights={() => { setTab("overview"); setEditing(true); }}
            onReviewed={analysis_status => onUpdate({ ...reference, analysis_status })} />
        </div>}
        {tab === "usage" && <div className="typography-drawer-content typography-lineage">
          <dl><dt>Reference ID</dt><dd>{reference.id}</dd><dt>Content hash</dt><dd><code>{reference.content_hash}</code></dd>
            <dt>Source attribution</dt><dd>{reference.source_attribution || "Not supplied"}</dd>
            <dt>Created</dt><dd>{new Date(reference.created_at).toLocaleString()}</dd>
            <dt>Campaign / keyword / skill</dt><dd>{reference.campaign_id || reference.keyword_id || reference.skill_name || "Workspace"}</dd></dl>
          <p>Original image remains in Managed Storage even when this reference is archived. No generation jobs are started from this drawer.</p>
        </div>}
      </div>
      {error && <p className="typography-error" role="alert">{error}</p>}
      <footer>
        {canManage ? <>
          <button type="button" disabled={busy || editing || reference.editorial_status === "archived"} onClick={() => setArchiveConfirm(true)}>Archive</button>
          {!editing && <button type="button" disabled={busy} onClick={() => setEditing(true)}>Edit metadata</button>}
          {!editing && reference.editorial_status !== "archived" && <>
            <button type="button" className="typography-vote typography-vote-pick" aria-pressed={reference.preference === "positive"}
              onClick={() => void onVote(reference, "picked")} title="Learn styles you like">✓ Pick</button>
            <button type="button" className="typography-vote typography-vote-reject" aria-pressed={reference.preference === "negative"}
              onClick={() => void onVote(reference, "rejected")} title="Learn styles you dislike">× Reject</button>
          </>}
        </> : <span>Read-only reference · your role cannot edit or approve</span>}
      </footer>
      {archiveConfirm && <div className="typography-archive-confirm" role="alertdialog" aria-label="Confirm archive">
        <strong>Archive this reference?</strong><p>It will leave the active library. Stored image files and other references remain intact.</p>
        <div><button ref={archiveCancelRef} disabled={busy} type="button" onClick={() => setArchiveConfirm(false)}>Cancel</button>
          <button disabled={busy} className="typography-danger" type="button" onClick={() => void archive()}>{busy ? "Archiving…" : "Archive reference"}</button></div>
      </div>}
    </section>
  </div>, document.body);
}