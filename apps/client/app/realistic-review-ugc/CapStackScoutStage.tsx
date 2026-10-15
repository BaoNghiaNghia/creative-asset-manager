import { useEffect, useMemo, useState } from "react";
import { DeferredImage } from "./DeferredImage";
import { RrugcStageHeader } from "./RrugcStageHeader";
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

const VISION_STATUS_OPTIONS = [
  ["all", "Any Gemini status"], ["pending", "Gemini pending"], ["analyzing", "Gemini analyzing"],
  ["ready", "Count ready"], ["needs_review", "Low confidence"],
  ["not_cap_stack", "Not a Cap Stack"], ["error", "Gemini error"],
] as const;

const DEFAULT_PAGE: CapStackPage = {
  items: [], total: 0, page: 1, page_size: 40,
  stats: { discovered: 0, approved: 0, rejected: 0, ai: 0, pending: 0 },
  scout: { enabled: false, running: false, last_result: null, next_scan_at: null },
};

function actionLabel(value: CapStackLabel): string {
  return value === "good" ? "Approved" : value === "bad" ? "Rejected" : value === "ai" ? "AI image" : "Pending";
}

export function CapStackScoutStage({ active }: { active: boolean }) {
  const [data, setData] = useState<CapStackPage>(DEFAULT_PAGE);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [label, setLabel] = useState("all");
  const [visionStatus, setVisionStatus] = useState("all");
  const [count, setCount] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<CapStackImage | null>(null);
  const [editableCount, setEditableCount] = useState("");
  const [view, setView] = useState<"grid" | "list">("grid");

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
        const next = await listCapStackImages({ page, pageSize: 40, q: debouncedQuery, label, capsCount: count, visionStatus });
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
      if (!document.hidden && !busy && !selected) void refresh();
    }, 15000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [active, page, label, visionStatus, count, debouncedQuery, busy, selected]);

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

  function openDetail(image: CapStackImage) {
    setSelected(image);
    setEditableCount(String(image.manual_caps_count ?? image.caps_detected ?? ""));
  }

  const buttons = (item: CapStackImage) => (
    <div className="rrugc-cap-stack-votes" role="group" aria-label={"Cap Stack feedback for " + item.id}>
      {(["good", "bad", "ai"] as const).map(value => (
        <button key={value} type="button" aria-pressed={item.manual_label === value}
          className={item.manual_label === value ? "is-active " + value : value}
          disabled={Boolean(busy)}
          title={value === "good" ? "Approve authentic Cap Stack" : value === "bad" ? "Reject unsuitable image" : "Reject AI-generated image"}
          onClick={() => void updateImage(item, { label: item.manual_label === value ? "clear" : value })}>
          {value === "good" ? "✓" : value === "bad" ? "×" : "AI"}
        </button>
      ))}
    </div>
  );

  const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
  const loadedCount = useMemo(() => data.items.length, [data.items]);
  return <div className="rrugc-cap-stack-stage">
    <RrugcStageHeader
      kicker="GATORHATS REVIEWS · STAGE 1"
      title="Cap Stack Scout"
      description="Shared Pinterest library. Gemini pool counts visible caps and highlights uncertain photos; human feedback remains authoritative."
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

    <div className="rrugc-cap-stack-summary" aria-label="Cap Stack Library metrics">
      {[
        ["Discovered", data.stats.discovered], ["Approved", data.stats.approved],
        ["Rejected", data.stats.rejected], ["AI", data.stats.ai], ["Pending", data.stats.pending],
      ].map(([name, value]) => <div key={name}><small>{name}</small><strong>{value}</strong></div>)}
      <div className="rrugc-cap-stack-learning">
        <small>Feedback learning</small><strong>Live query ranking</strong>
        <span>✓ / × / AI influence the next Scout searches.</span>
      </div>
    </div>

    <div className="rrugc-cap-stack-vision" aria-label="Shared Gemini pool analysis status">
      <strong>Gemini pool · Image Analysis</strong>
      <span>Count ready: {data.vision?.ready ?? 0}</span>
      <span>Waiting: {(data.vision?.pending ?? 0) + (data.vision?.analyzing ?? 0)}</span>
      <span>Review count: {data.vision?.needs_review ?? 0}</span>
      <span>Not Cap Stack: {data.vision?.not_cap_stack ?? 0}</span>
      <span>Suspected AI: {data.vision?.ai_suspected ?? 0}</span>
      <span>Errors: {data.vision?.error ?? 0}</span>
    </div>

    <div className="rrugc-cap-stack-toolbar">
      <div className="rrugc-cap-stack-search">
        <RrugcActionIcon name="scan"/>
        <input aria-label="Search Cap Stack library" value={query}
          placeholder="Search images or sources…" onChange={event => { setQuery(event.target.value); setPage(1); }}/>
      </div>
      <label>Status<select value={label} onChange={event => { setLabel(event.target.value); setPage(1); }}>
        {LABELS.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <label>Gemini status<select value={visionStatus} onChange={event => { setVisionStatus(event.target.value); setPage(1); }}>
        {VISION_STATUS_OPTIONS.map(([key, label]) => <option value={key} key={key}>{label}</option>)}
      </select></label>
      <label>Caps detected<select value={count ?? ""} onChange={event => { setCount(event.target.value ? Number(event.target.value) : null); setPage(1); }}>
        <option value="">Any number</option>
        {Array.from({ length: 15 }, (_, index) => index + 1).map(value => <option value={value} key={value}>{value} caps</option>)}
      </select></label>
      <label>View<select value={view} onChange={event => setView(event.target.value as "grid" | "list")}>
        <option value="grid">Grid</option><option value="list">List</option>
      </select></label>
      <span>{data.total} results · {loadedCount} loaded</span>
    </div>

    {error && <p className="rrugc-cap-stack-error" role="alert">{error}</p>}
    {loading && !data.items.length && <p className="rrugc-cap-stack-loading" role="status">Loading Cap Stack Library…</p>}
    {!loading && !data.items.length && <div className="rrugc-cap-stack-empty">
      <RrugcActionIcon name="folder"/>
      <h3>{data.stats.discovered ? "No matches for the selected filters" : "Your shared Cap Stack library is ready"}</h3>
      <p>{data.stats.discovered
        ? "Change the status, number of caps or search text to view other images."
        : "Enable Cap Stack Scout here, then start Cap Stack Scout in Windows Scout Manager. Images will arrive here when Pinterest discovery finds new Pins."}</p>
      <small>Keyword: Cap Stack · Gemini runs through CAM shared pool; image processing retries when capacity is available.</small>
    </div>}
    <div className={"rrugc-cap-stack-gallery view-" + view} aria-label="Cap Stack image library">
      {data.items.map(item => <article key={item.id} className="rrugc-cap-stack-card">
        <button type="button" className="rrugc-cap-stack-preview" onClick={() => openDetail(item)}
          aria-label={"View Cap Stack image " + item.id}>
          <DeferredImage src={item.image_url} alt={item.alt_text || "Pinterest Cap Stack reference"} loading="lazy" referrerPolicy="no-referrer"/>
          <span className="rrugc-cap-stack-count">
            {item.manual_caps_count ?? item.caps_detected ?? "?"} caps
          </span>
        </button>
        <div className="rrugc-cap-stack-card-body">
          <div className="rrugc-cap-stack-card-title"><strong>{item.source_query || "Cap Stack"}</strong>
            <span className={"label-" + (item.manual_label || "pending")}>{actionLabel(item.manual_label)}</span></div>
          <p>{item.alt_text || "Pinterest reference · pending verification"}</p>
          <div className="rrugc-cap-stack-ai-status">
            <span className={"status-" + item.count_status}>{item.count_status === "ready" ? "Gemini: counted" : item.count_status === "needs_review" ? "Gemini: low confidence" : item.count_status === "not_cap_stack" ? "Not a Cap Stack" : item.count_status === "error" ? "Gemini error" : item.count_status === "analyzing" ? "Gemini: analyzing" : "Gemini: queued"}</span>
            {item.ai_suspected === true && <span className="suspected">AI suspected · review</span>}
          </div>
          <div className="rrugc-cap-stack-card-footer">{buttons(item)}
            <a href={item.pin_url} target="_blank" rel="noreferrer noopener" aria-label="Open original Pinterest Pin">Pinterest ↗</a></div>
        </div>
      </article>)}
    </div>

    {data.total > data.page_size && <div className="rrugc-cap-stack-pagination">
      <button type="button" disabled={page <= 1} onClick={() => setPage(previous => previous - 1)}>Previous</button>
      <span>Page {page} of {pageCount}</span>
      <button type="button" disabled={page >= pageCount} onClick={() => setPage(previous => previous + 1)}>Next</button>
    </div>}

    {selected && <div className="rrugc-cap-stack-modal-overlay" role="presentation"
      onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}>
      <div className="rrugc-cap-stack-modal" role="dialog" aria-modal="true"
        aria-label="Cap Stack image details">
        <header><div><strong>Cap Stack · Image details</strong><small>{selected.source_query || "Cap Stack"} · {actionLabel(selected.manual_label)}</small></div>
          <button type="button" onClick={() => setSelected(null)} aria-label="Close Cap Stack details">×</button></header>
        <div className="rrugc-cap-stack-modal-content">
          <img src={selected.image_url} alt={selected.alt_text || "Cap Stack reference"} referrerPolicy="no-referrer"/>
          <div className="rrugc-cap-stack-modal-aside">
            <label>Number of caps in this image<input type="number" value={editableCount}
              min={1} max={50} placeholder="Count caps"
              onChange={event => setEditableCount(event.target.value)} /></label>
            <button type="button" disabled={Boolean(busy) || !Number.isInteger(Number(editableCount)) || Number(editableCount) < 1 || Number(editableCount) > 50}
              onClick={() => void updateImage(selected, { caps_count: Number(editableCount) })}>Save cap count</button>
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
