import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DeferredImage } from "./DeferredImage";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { StableMasonryGrid, stableMasonryRatio } from "./StableMasonryGrid";
import { RrugcActionIcon } from "./RrugcActionIcon";
import {
  listCapStackImages, setCapStackEnabled, setCapStackImageFeedback, retryCapStackVision,
  type CapStackImage, type CapStackLabel, type CapStackPage,
} from "./api";
import "./CapStackScoutStage.css";

const LABELS = [
  { id: "all", name: "All images" }, { id: "pending", name: "Pending" },
  { id: "good", name: "Approved" }, { id: "bad", name: "Rejected" },
  { id: "ai", name: "AI images" },
];

const DEFAULT_PAGE: CapStackPage = {
  items: [], total: 0, page: 1, page_size: 40,
  stats: { discovered: 0, approved: 0, rejected: 0, ai: 0, pending: 0 },
  scout: { enabled: false, running: false, last_result: null, next_scan_at: null },
};

function actionLabel(value: CapStackLabel): string {
  return value === "good" ? "Approved" : value === "bad" ? "Rejected" : value === "ai" ? "AI image" : "Pending";
}

function validCapCount(value: number | null | undefined): number | null {
  return Number.isInteger(value) && value != null && value >= 2 && value <= 6 ? value : null;
}

function quickApprovalCount(item: CapStackImage): number | null {
  // Human-verified counts take precedence. A ready, in-range Gemini estimate
  // may also be explicitly approved with one click. Other images need review.
  return validCapCount(item.manual_caps_count) ??
    (item.count_status === "ready" && item.is_cap_stack !== false
      ? validCapCount(item.caps_detected) : null);
}

function visionLabel(item: CapStackImage): string {
  switch (item.count_status) {
    case "ready": return "Gemini: 2–6 caps";
    case "needs_review": return "Gemini: low confidence";
    case "out_of_range": return "Over 6 caps · excluded";
    case "not_cap_stack": return "Not a Cap Stack";
    case "error": return "Gemini error";
    case "analyzing": return "Gemini: analyzing";
    default: return "Gemini: queued";
  }
}

// Cap Stack's API has no native image dimensions yet. Seed a deterministic
// skeleton ratio for each Pin, then discover its actual ratio on lazy decode.
// Keep column assignment fixed while images load or feedback changes.
const CAP_STACK_RATIOS = [0.68, 0.8, 0.94, 1.12, 1.3, 1.48];
const imageRatioCache = new Map<string, number>();

function layoutRatio(item: CapStackImage): number {
  let hash = 0;
  for (let i = 0; i < item.id.length; i += 1) hash = (hash * 31 + item.id.charCodeAt(i)) | 0;
  return CAP_STACK_RATIOS[(hash >>> 0) % CAP_STACK_RATIOS.length];
}

function CapStackCardMedia({ item, actions, onOpen }: {
  item: CapStackImage; actions: ReactNode; onOpen: () => void;
}) {
  const [ratio, setRatio] = useState(() => imageRatioCache.get(item.image_url) ?? layoutRatio(item));
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);

  useEffect(() => {
    setRatio(imageRatioCache.get(item.image_url) ?? layoutRatio(item));
    setLoadedUrl(null);
  }, [item.id, item.image_url]);

  return <div className={"rrugc-cap-stack-media" + (loadedUrl === item.image_url ? " is-loaded" : " is-loading")}
    style={{ aspectRatio: ratio }} data-image-state={loadedUrl === item.image_url ? "loaded" : "loading"}>
    <span className="rrugc-cap-stack-skeleton" aria-hidden="true"/>
    <DeferredImage src={item.image_url} alt={item.alt_text || "Pinterest Cap Stack reference"}
      loading="lazy" rootMargin="320px 0px" referrerPolicy="no-referrer"
      onLoad={event => {
        const image = event.currentTarget;
        if (image.naturalWidth > 0 && image.naturalHeight > 0) {
          const actualRatio = stableMasonryRatio(image.naturalWidth, image.naturalHeight);
          if (!imageRatioCache.has(item.image_url) && imageRatioCache.size >= 500) {
            imageRatioCache.delete(imageRatioCache.keys().next().value!);
          }
          imageRatioCache.set(item.image_url, actualRatio);
          setRatio(actualRatio);
        }
        setLoadedUrl(item.image_url);
      }}/>
    <button type="button" className="rrugc-cap-stack-preview" onClick={onOpen}
      aria-label={"View Cap Stack image " + item.id}/>
    <span className="rrugc-cap-stack-count" title={visionLabel(item)}>
      {item.manual_caps_count ?? item.caps_detected ?? "?"} caps
    </span>
    {actions}
  </div>;
}

export function CapStackScoutStage({ active }: { active: boolean }) {
  const [data, setData] = useState<CapStackPage>(DEFAULT_PAGE);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [label, setLabel] = useState("all");
  const [count, setCount] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<CapStackImage | null>(null);
  const [editableCount, setEditableCount] = useState("");
  const [approvalRequestedFor, setApprovalRequestedFor] = useState<string | null>(null);
  // The polling effect must not restart on every feedback/busy/modal update.
  // Use current refs in the interval so a stale list response does not
  // immediately overwrite a just-committed human review.
  const busyRef = useRef(busy);
  const selectedRef = useRef(selected);
  useEffect(() => {
    busyRef.current = busy;
    selectedRef.current = selected;
  }, [busy, selected]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!active) return;
    let mounted = true;
    let inflight = false;
    const refresh = async () => {
      if (inflight) return;
      inflight = true;
      setLoading(true);
      try {
        const next = await listCapStackImages({ page, pageSize: 40, q: debouncedQuery, label, capsCount: count, visionStatus: "all" });
        if (mounted) { setData(next); setError(""); }
      } catch (reason) {
        if (mounted) setError(reason instanceof Error ? reason.message : "Cap Stack Library could not load.");
      } finally {
        inflight = false;
        if (mounted) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden && !busyRef.current && !selectedRef.current) void refresh();
    }, 15000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [active, page, label, count, debouncedQuery]);

  async function updateImage(image: CapStackImage, body: { label?: "good" | "bad" | "ai" | "clear"; caps_count?: number }) {
    if (busy) return;
    setBusy(image.id);
    try {
      const updated = await setCapStackImageFeedback(image.id, body);
      setData(current => ({
        ...current,
        items: current.items.map(item => item.id === image.id ? updated : item),
        stats: {
          ...current.stats,
          ...(() => {
            const stats = { ...current.stats };
            const old = image.manual_label || "pending";
            const next = updated.manual_label || "pending";
            const field = (key: string): keyof typeof stats =>
              key === "good" ? "approved" : key === "bad" ? "rejected" : key === "ai" ? "ai" : "pending";
            if (old !== next) {
              stats[field(old)] = Math.max(0, stats[field(old)] - 1);
              stats[field(next)] += 1;
            }
            return stats;
          })(),
        },
      }));
      setSelected(current => current?.id === updated.id ? updated : current);
      if (body.label === "good") setApprovalRequestedFor(null);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save Cap Stack feedback.");
    } finally { setBusy(""); }
  }

  async function retryVision(image: CapStackImage) {
    if (busy) return;
    setBusy(image.id);
    try {
      const response = await retryCapStackVision(image.id);
      setData(current => ({ ...current, items: current.items.map(item => item.id === image.id ? response.image : item) }));
      setSelected(current => current?.id === image.id ? response.image : current);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to retry Gemini vision analysis.");
    } finally { setBusy(""); }
  }

  async function setEnabled(enabled: boolean) {
    if (busy) return;
    setBusy("setting");
    try {
      await setCapStackEnabled(enabled);
      setData(current => ({ ...current, scout: { ...current.scout, enabled, running: enabled ? current.scout.running : false } }));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update Cap Stack Scout.");
    } finally { setBusy(""); }
  }

  function openDetail(image: CapStackImage, approve = false) {
    setSelected(image);
    setEditableCount(String(validCapCount(image.manual_caps_count) ??
      validCapCount(image.caps_detected) ?? ""));
    setApprovalRequestedFor(approve ? image.id : null);
    setError("");
  }

  function submitFeedback(item: CapStackImage, value: "good" | "bad" | "ai", overlay: boolean) {
    if (item.manual_label === value) {
      void updateImage(item, { label: "clear" });
      return;
    }
    if (value !== "good") {
      void updateImage(item, { label: value });
      return;
    }
    if (overlay) {
      const count = quickApprovalCount(item);
      if (count === null) {
        // Never send a request known to violate the API's 2–6 cap invariant.
        openDetail(item, true);
        return;
      }
      void updateImage(item, { label: "good", caps_count: count });
      return;
    }
    const count = validCapCount(editableCount.trim() === "" ? null : Number(editableCount));
    if (count === null) {
      setApprovalRequestedFor(item.id);
      setError("Confirm a visible cap count from 2 to 6 before approving.");
      return;
    }
    void updateImage(item, { label: "good", caps_count: count });
  }

  const buttons = (item: CapStackImage, overlay = false) => (
    <div className={"rrugc-cap-stack-votes" + (overlay ? " is-overlay" : "")}
      role="group" aria-label={"Cap Stack feedback for " + item.id} aria-busy={busy === item.id}>
      {(["good", "bad", "ai"] as const).map(value => {
        const action = value === "good" ? "Approve authentic Cap Stack" : value === "bad" ? "Reject unsuitable image" : "Mark AI-generated image";
        const pressed = item.manual_label === value;
        return <button key={value} type="button" aria-pressed={pressed}
          aria-label={pressed ? "Clear " + action.toLowerCase() : action}
          className={pressed ? "is-active " + value : value}
          disabled={Boolean(busy)}
          title={pressed ? "Clear " + action.toLowerCase() : action}
          onClick={() => submitFeedback(item, value, overlay)}>
          {value === "good" ? "✓" : value === "bad" ? "×" : "AI"}
        </button>;
      })}
    </div>
  );

  const renderCard = (item: CapStackImage) => (
    <article key={item.id} className={"rrugc-cap-stack-card label-" + (item.manual_label || "pending")}
      data-cap-stack-id={item.id}>
      <CapStackCardMedia item={item} onOpen={() => openDetail(item)} actions={buttons(item, true)}/>
      <div className="rrugc-cap-stack-card-footer">
        <strong title={item.alt_text || item.source_query || "Cap Stack"}>{item.source_query || "Cap Stack"}</strong>
        <a href={item.pin_url} target="_blank" rel="noreferrer noopener"
          aria-label="Open original Pinterest Pin">Pinterest ↗</a>
        <span className={"rrugc-cap-stack-card-state label-" + (item.manual_label || "pending")}>
          {actionLabel(item.manual_label)} · {visionLabel(item)}
          {item.ai_suspected === true ? " · AI suspected" : ""}
        </span>
      </div>
    </article>
  );

  const confirmedModalCount = validCapCount(editableCount.trim() === "" ? null : Number(editableCount));
  const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
  const loadedCount = useMemo(() => data.items.length, [data.items]);
  return <div className="rrugc-cap-stack-stage">
    <RrugcStageHeader
      className="rrugc-cap-stack-heading rrugc-card"
      kicker="REVIEWS · STAGE 1 · PINTEREST SCOUT"
      title="Cap Stack Scout"
      description="Pinterest stacks of 2–6 caps · Gemini-assisted image review with human feedback priority."
      actions={<div className="rrugc-cap-stack-top">
        <span className={"rrugc-cap-stack-state " + (data.scout.enabled ? "enabled" : "")}>
          <span aria-hidden="true">●</span> {data.scout.running ? "Scanning" : data.scout.enabled ? "Enabled · waiting" : "Paused"}
        </span>
        <button type="button" className={data.scout.enabled ? "rrugc-cap-stack-pause" : "rrugc-cap-stack-start"}
          disabled={Boolean(busy)}
          onClick={() => void setEnabled(!data.scout.enabled)}>
          <RrugcActionIcon name={data.scout.enabled ? "pause" : "play"}/>
          {data.scout.enabled ? "Pause Scout" : "Enable Scout"}
        </button>
      </div>}
    />

    <div className="rrugc-cap-stack-controls" aria-label="Cap Stack library controls">
      <div className="rrugc-cap-stack-overview" aria-label="Cap Stack library summary">
        <div className="rrugc-cap-stack-summary" aria-label="Cap Stack Library metrics">
          {[
            ["Discovered", data.stats.discovered], ["Approved", data.stats.approved],
            ["Rejected", data.stats.rejected], ["AI", data.stats.ai], ["Pending", data.stats.pending],
          ].map(([name, value]) => <span className={"rrugc-cap-stack-metric metric-" + String(name).toLowerCase()}
            key={name}><small>{name}</small><strong>{value}</strong></span>)}
        </div>
        <details className="rrugc-cap-stack-vision" aria-label="Shared Gemini pool analysis status">
          <summary aria-label="Show Gemini analysis details">
            <strong>Gemini</strong>
            <span>Ready <b>{data.vision?.ready ?? 0}</b></span>
            <span>Waiting <b>{(data.vision?.pending ?? 0) + (data.vision?.analyzing ?? 0)}</b></span>
          </summary>
          <div className="rrugc-cap-stack-vision-details">
            <span>Count ready: {data.vision?.ready ?? 0}</span>
            <span>Waiting: {(data.vision?.pending ?? 0) + (data.vision?.analyzing ?? 0)}</span>
            <span>Review count: {data.vision?.needs_review ?? 0}</span>
            <span>Not Cap Stack: {data.vision?.not_cap_stack ?? 0}</span>
            <span>Over 6 caps: {data.vision?.out_of_range ?? 0}</span>
            <span>Suspected AI: {data.vision?.ai_suspected ?? 0}</span>
            <span>Errors: {data.vision?.error ?? 0}</span>
          </div>
        </details>
      </div>

      <div className="rrugc-cap-stack-toolbar">
        <div className="rrugc-cap-stack-search">
          <RrugcActionIcon name="scan"/>
          <input aria-label="Search Cap Stack library" value={query}
            placeholder="Search images or sources…" onChange={event => { setQuery(event.target.value); setPage(1); }}/>
        </div>
        <label><span className="rrugc-cap-stack-sr-only">Status</span>
          <select aria-label="Filter by review status" value={label}
            onChange={event => { setLabel(event.target.value); setPage(1); }}>
            {LABELS.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label><span className="rrugc-cap-stack-sr-only">Caps detected</span>
          <select aria-label="Filter by cap count" value={count ?? ""}
            onChange={event => { setCount(event.target.value ? Number(event.target.value) : null); setPage(1); }}>
            <option value="">Any number</option>
            {Array.from({ length: 5 }, (_, index) => index + 2).map(value => <option value={value} key={value}>{value} caps</option>)}
          </select>
        </label>
        <span className="rrugc-cap-stack-results">{data.total} results · {loadedCount} loaded</span>
      </div>
    </div>

    {error && <p className="rrugc-cap-stack-error" role="alert">{error}</p>}
    {loading && !data.items.length && <div className="rrugc-cap-stack-placeholder-gallery"
      role="status" aria-label="Loading Cap Stack Library">
      {Array.from({ length: 12 }, (_, index) => <div className="rrugc-cap-stack-placeholder"
        key={index} style={{ aspectRatio: CAP_STACK_RATIOS[index % CAP_STACK_RATIOS.length] }}>
        <span className="rrugc-cap-stack-placeholder-footer"/>
      </div>)}
    </div>}
    {!loading && !data.items.length && <div className="rrugc-cap-stack-empty">
      <RrugcActionIcon name="folder"/>
      <h3>{data.stats.discovered ? "No matches for the selected filters" : "Your shared Cap Stack library is ready"}</h3>
      <p>{data.stats.discovered
        ? "Change the status, number of caps or search text to view other images."
        : "Enable Cap Stack Scout here, then start Cap Stack Scout in Windows Scout Manager. Images will arrive here when Pinterest discovery finds new Pins."}</p>
      <small>Keyword: Cap Stack · Gemini runs through CAM shared pool; image processing retries when capacity is available.</small>
    </div>}
    <StableMasonryGrid className="rrugc-cap-stack-gallery view-masonry"
      items={data.items} getKey={item => item.id} getRatio={layoutRatio}
      renderItem={renderCard} minColumnWidth={180} maxColumns={10}
      ariaLabel="Cap Stack image library"/>

    {data.total > data.page_size && <div className="rrugc-cap-stack-pagination">
      <button type="button" disabled={page <= 1} onClick={() => setPage(previous => previous - 1)}>Previous</button>
      <span>Page {page} of {pageCount}</span>
      <button type="button" disabled={page >= pageCount} onClick={() => setPage(previous => previous + 1)}>Next</button>
    </div>}

    {selected && <div className="rrugc-cap-stack-modal-overlay" role="presentation"
      onMouseDown={event => { if (event.target === event.currentTarget) { setSelected(null); setApprovalRequestedFor(null); } }}>
      <div className="rrugc-cap-stack-modal" role="dialog" aria-modal="true"
        aria-label="Cap Stack image details">
        <header><div><strong>Cap Stack · Image details</strong><small>{selected.source_query || "Cap Stack"} · {actionLabel(selected.manual_label)}</small></div>
          <button type="button" onClick={() => { setSelected(null); setApprovalRequestedFor(null); }} aria-label="Close Cap Stack details">×</button></header>
        <div className="rrugc-cap-stack-modal-content">
          <img src={selected.image_url} alt={selected.alt_text || "Cap Stack reference"} referrerPolicy="no-referrer"/>
          <div className="rrugc-cap-stack-modal-aside">
            {approvalRequestedFor === selected.id && <p className="rrugc-cap-stack-approval-note" role="status">
              Check the photo and confirm 2–6 visible caps to approve. Gemini may still be pending.
            </p>}
            <label>Number of caps in this image<input type="number" value={editableCount}
              min={2} max={6} step={1} placeholder="2–6 caps"
              autoFocus={approvalRequestedFor === selected.id}
              onChange={event => { setEditableCount(event.target.value); setError(""); }} /></label>
            {error && <p className="rrugc-cap-stack-modal-error" role="alert">{error}</p>}
            <div className="rrugc-cap-stack-count-actions">
              <button type="button" disabled={Boolean(busy) || confirmedModalCount === null}
                onClick={() => { if (confirmedModalCount !== null) void updateImage(selected, { caps_count: confirmedModalCount }); }}>
                Save cap count
              </button>
              {selected.manual_label !== "good" && <button type="button" className="is-approve"
                disabled={Boolean(busy) || confirmedModalCount === null}
                onClick={() => { if (confirmedModalCount !== null) void updateImage(selected, { label: "good", caps_count: confirmedModalCount }); }}>
                Confirm count & approve
              </button>}
            </div>
            <div className="rrugc-cap-stack-ai-details">
              <strong>Gemini vision · {selected.count_status.replace(/_/g, " ")}</strong>
              <span>Visible caps: {selected.caps_detected ?? "Not analyzed"} · Confidence: {selected.count_confidence == null ? "—" : Math.round(selected.count_confidence * 100) + "%"}</span>
              <span>Stack detected: {selected.is_cap_stack == null ? "Unknown" : selected.is_cap_stack ? "Yes" : "No"}</span>
              <span>AI-generated suspicion: {selected.ai_suspected == null ? "Not assessed" : selected.ai_suspected ? "Possible — human review required" : "No obvious signs"}</span>
              {selected.visual_summary && <p>{selected.visual_summary}</p>}
              {selected.analysis_model && <small>Gemini model: {selected.analysis_model}</small>}
              {selected.analysis_error_code && <small>Analysis error: {selected.analysis_error_code}</small>}
            </div>
            {selected.count_status !== "analyzing" && selected.count_status !== "pending" &&
              <button type="button" disabled={Boolean(busy)} onClick={() => void retryVision(selected)}>Retry Gemini analysis</button>}
            {buttons(selected)}
            <a href={selected.pin_url} target="_blank" rel="noreferrer noopener">Open original Pinterest Pin ↗</a>
          </div>
        </div>
      </div>
    </div>}
  </div>;
}