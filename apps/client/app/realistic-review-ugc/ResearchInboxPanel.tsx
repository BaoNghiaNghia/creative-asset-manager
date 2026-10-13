import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

type ResearchGlyphName =
  | "radar" | "close" | "database" | "check" | "clock" | "globe"
  | "refresh" | "upload" | "sync" | "list" | "external" | "pause"
  | "play" | "search" | "warning" | "arrow";

function ResearchGlyph({ name, size = 17 }: { name: ResearchGlyphName; size?: number }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true"
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {name === "radar" && <><circle cx="11" cy="11" r="8"/><circle cx="11" cy="11" r="3"/><path d="m11 11 6-6m-1.7 10.7L21 21"/></>}
    {name === "close" && <path d="M5 5 19 19M19 5 5 19"/>}
    {name === "database" && <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.65 3.6 3 8 3s8-1.35 8-3V5M4 12c0 1.65 3.6 3 8 3s8-1.35 8-3"/></>}
    {name === "check" && <><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>}
    {name === "clock" && <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></>}
    {name === "globe" && <><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 4 6 4 9s-1 6-4 9M12 3c-3 3-4 6-4 9s1 6 4 9"/></>}
    {name === "refresh" && <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9a7 7 0 0 1 12-2l2.5 5M18.5 15a7 7 0 0 1-12 2L4 12"/></>}
    {name === "upload" && <><path d="M12 16V3m-5 5 5-5 5 5M4 16v4h16v-4"/></>}
    {name === "sync" && <><path d="M20 7h-9M20 7l-4-4m4 4-4 4M4 17h9M4 17l4-4m-4 4 4 4"/></>}
    {name === "list" && <><path d="M9 6h12M9 12h12M9 18h12"/><path d="m3 6 1 1 2-2m-3 7 1 1 2-2m-3 7 1 1 2-2"/></>}
    {name === "external" && <><path d="M13 5h6v6M19 5l-9 9"/><path d="M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></>}
    {name === "pause" && <><rect x="4" y="4" width="16" height="16" rx="5"/><path d="M10 8v8m4-8v8"/></>}
    {name === "play" && <><circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/></>}
    {name === "search" && <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>}
    {name === "warning" && <><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v4m0 4h.01"/></>}
    {name === "arrow" && <path d="M4 12h16m-6-6 6 6-6 6"/>}
  </svg>;
}
import "./ResearchInboxPanel.css";

export type ResearchSignal = {
  id: string; source: string; platform?: string; channel: string; title: string; excerpt: string;
  url: string; status: string; keyword: string | null; created_at: string;
};

export type AutoDiscoverStatus = {
  state: "not_configured" | "paused" | "ready" | "busy" | "offline" | "error";
  connected: boolean; server: string; source: string; last_seen_at: string | null;
  last_error_code: string | null; total_evidence: number; pending_review: number; stage0_auto_keywords?: number;
  platforms: { pinterest: number; amazon: number; etsy: number };
};
const DISCOVERY_LABELS: Record<AutoDiscoverStatus["state"], string> = {
  not_configured: "Chưa kết nối", paused: "Đã tạm dừng",
  ready: "Sẵn sàng", busy: "Đang tìm kiếm", offline: "Mất kết nối", error: "Có lỗi",
};

export function discoveryErrorMessage(status: AutoDiscoverStatus | null): string {
  if (status?.state !== "error") return "";
  if (status.last_error_code === "exa_rate_limited") {
    return "Exa đang giới hạn tìm kiếm. VPS sẽ tự thử lại sau cooldown; kết quả đã lưu vẫn được giữ.";
  }
  if (status.last_error_code === "tavily_rate_limited") {
    return "Tavily Keyless đã đạt hạn mức miễn phí. VPS sẽ chờ cooldown; dữ liệu đã lưu vẫn được giữ.";
  }
  if (status.last_error_code === "tavily_keyless_denied") {
    return "Tavily Keyless đang từ chối tìm kiếm miễn phí. VPS sẽ tự thử lại theo lịch, không phát sinh phí.";
  }
  if (status.last_error_code === "brave_rate_limited") {
    return "Brave Search đã hết quota hoặc giới hạn tạm thời. VPS sẽ dùng Exa có API key nếu đã cấu hình, hoặc tự thử lại.";
  }
  if (status.last_error_code === "brave_not_configured" || status.last_error_code === "brave_auth_rejected") {
    return "Brave Search chưa có API key hợp lệ. Hãy kiểm tra cấu hình riêng của VPS.";
  }
  if (status.last_error_code === "cam_sync_retryable") {
    return "Chưa đồng bộ được về CAM. Dữ liệu vẫn nằm trong hàng chờ an toàn để thử lại.";
  }
  return "VPS gặp lỗi tìm kiếm hoặc kết nối. Kiểm tra log Agent-Reach để biết chi tiết.";
}

const API_ROOT = "/api/v1/realistic-review-ugc/research";

async function researchRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_ROOT + path, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    throw new Error(response.status === 403 ? "Bạn không có quyền duyệt Research Inbox."
      : response.status === 404 ? "Research Intelligence chưa được kích hoạt."
      : `Research request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function isPublicHttpsUrl(value: unknown): boolean {
  if (typeof value !== "string" || value.length < 8 || value.length > 2048) return false;
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
    return url.protocol === "https:" && !url.username && !url.password
      && (!url.port || url.port === "443") && Boolean(hostname)
      && hostname !== "localhost" && !hostname.endsWith(".local")
      && !hostname.endsWith(".internal") && !/^[0-9.]+$/.test(hostname)
      && !hostname.startsWith("[");
  } catch {
    return false;
  }
}

function isMarketplaceListing(source: string, inputUrl: string): boolean {
  if (!["pinterest", "etsy", "amazon"].includes(source)) return true;
  try {
    const url = new URL(inputUrl);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    const allowed = host === source + ".com" || host === "www." + source + ".com";
    if (!allowed) return false;
    const patterns: Record<string, RegExp> = {
      pinterest: /^\/pin\/\d+(?:\/|$)/i,
      etsy: /^\/listing\/\d+(?:\/|$)/i,
      amazon: /^\/(?:dp|gp\/product)\/[a-z0-9]{10}(?:\/|$)/i,
    };
    return patterns[source].test(url.pathname);
  } catch { return false; }
}

export function parseResearchImport(value: string): Array<Record<string, unknown>> {
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed) || !parsed.length || parsed.length > 50) {
    throw new Error("Tối đa 50 research signals cho mỗi lần nhập.");
  }
  for (const signal of parsed) {
    if (!signal || typeof signal !== "object" || Array.isArray(signal)
      || !["rss", "github", "youtube", "web", "pinterest", "etsy", "amazon"].includes((signal as Record<string, unknown>).source as string)
      || typeof (signal as Record<string, unknown>).title !== "string"
      || !String((signal as Record<string, unknown>).title).trim()
      || String((signal as Record<string, unknown>).title).length > 240
      || !isPublicHttpsUrl((signal as Record<string, unknown>).url)
      || !isMarketplaceListing(String((signal as Record<string, unknown>).source), String((signal as Record<string, unknown>).url))) {
      throw new Error("Dữ liệu research phải có source, title và URL HTTPS hợp lệ.");
    }
  }
  return parsed as Array<Record<string, unknown>>;
}

export function ResearchInboxPanel({ open, onClose, onKeywordApproved }: {
  open: boolean;
  onClose: () => void;
  onKeywordApproved?: () => void;
}) {
  const modalRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [items, setItems] = useState<ResearchSignal[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [keywords, setKeywords] = useState<Record<string, string>>({});
  const [exportJson, setExportJson] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [autoDiscover, setAutoDiscover] = useState<AutoDiscoverStatus | null>(null);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const result = await researchRequest<{ items: ResearchSignal[] }>("/signals?status=pending&limit=25",
        signal ? { signal } : undefined);
      if (signal?.aborted) return;
      setItems(result.items);
      setUnavailable(false);
      try {
        const current = await researchRequest<AutoDiscoverStatus>("/auto-discover/status", signal ? { signal } : undefined);
        if (!signal?.aborted) setAutoDiscover(current);
      } catch { if (!signal?.aborted) setAutoDiscover(null); }
    } catch (e) {
      if (signal?.aborted) return;
      const message = e instanceof Error ? e.message : "Không tải được Research Inbox.";
      if (message.includes("chưa được kích hoạt")) setUnavailable(true);
      else setError(message);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const tabbable = Array.from(modalRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])',
      )).filter(element => element.getClientRects().length > 0 || element === closeRef.current);
      if (!tabbable.length) return;
      const first = tabbable[0];
      const last = tabbable[tabbable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = oldOverflow;
      previousFocus?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [open, refresh]);

  async function toggleDiscoveryPause() {
    if (!autoDiscover?.connected || discoveryBusy) return;
    setDiscoveryBusy(true);
    setError("");
    try {
      const changed = await researchRequest<AutoDiscoverStatus>("/auto-discover/control", {
        method: "POST", body: JSON.stringify({ paused: autoDiscover.state !== "paused" }),
      });
      setAutoDiscover(changed);
      setNotice(changed.state === "paused"
        ? "Đã tạm dừng Auto Discover trên VPS. Scout Windows không bị ảnh hưởng."
        : "Đã cho phép VPS tiếp tục lịch tìm kiếm. Scout Windows không bị ảnh hưởng.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không thay đổi được trạng thái Auto Discover.");
    } finally { setDiscoveryBusy(false); }
  }

  async function importBatch() {
    setError(""); setNotice(""); setBusyId("import");
    try {
      const signals = parseResearchImport(importText);
      const result = await researchRequest<{ added: number; duplicates: number }>("/signals/import", {
        method: "POST", body: JSON.stringify({ signals }),
      });
      setNotice(`Đã thêm ${result.added}, trùng ${result.duplicates}. Chưa gửi đến AEBrowse.`);
      setImportText(""); setImportOpen(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không nhập được research.");
    } finally { setBusyId(null); }
  }

  async function decide(row: ResearchSignal, decision: "approved" | "rejected") {
    setError(""); setNotice(""); setBusyId(row.id);
    try {
      await researchRequest(`/signals/${encodeURIComponent(row.id)}/review`, {
        method: "POST", body: JSON.stringify({
          decision, keyword: decision === "approved" ? keywords[row.id] || "" : null,
        }),
      });
      setNotice(decision === "approved"
        ? "Đã thêm keyword vào Stage 0 (chờ lấy search volume, chưa gọi AEBrowse)." : "Đã bỏ tín hiệu khỏi hàng chờ.");
      if (decision === "approved") onKeywordApproved?.();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không cập nhật được tín hiệu.");
    } finally { setBusyId(null); }
  }

  async function applyApprovedToStage0() {
    setError(""); setBusyId("apply");
    try {
      const result = await researchRequest<{ added: number; existing: number; checked: number }>("/stage0-apply", { method: "POST" });
      setNotice(`Đã đồng bộ ${result.added} keyword vào Stage 0; ${result.existing} keyword đã tồn tại. Không gọi AEBrowse.`);
      onKeywordApproved?.();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đồng bộ được keyword đã duyệt.");
    } finally { setBusyId(null); }
  }

  async function previewExport() {
    setError(""); setBusyId("export");
    try {
      const payload = await researchRequest<{ keywords: string[]; force: false }>("/stage0-export");
      setExportJson(JSON.stringify(payload, null, 2));
      setNotice(`${payload.keywords.length} keyword đã duyệt, sẵn sàng cho Stage 0.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Không xuất được keywords."); }
    finally { setBusyId(null); }
  }

  if (!open) return null;

  return createPortal(<div className="rrugc-research-backdrop" role="presentation"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="rrugc-research-inbox" role="dialog" aria-modal="true"
      aria-label="Agent-Reach Research Intelligence" ref={modalRef}>
      <header className="rrugc-research-inbox-head">
        <div className="rrugc-research-header-icon"><ResearchGlyph name="radar" size={27}/></div>
        <div className="rrugc-research-title-block">
          <span className="rrugc-research-eyebrow">AGENT-REACH <span>·</span> RESEARCH INTELLIGENCE</span>
          <h2>Research Inbox</h2>
          <p>GatorHats Products · Khám phá xu hướng, kiểm duyệt nguồn và chuyển keyword vào Stage 0.</p>
        </div>
        <span className={"rrugc-discovery-state state-" + (autoDiscover?.state || "unknown")}>
          <span className="rrugc-research-state-dot" aria-hidden="true"/>
          {autoDiscover ? DISCOVERY_LABELS[autoDiscover.state] : "Đang kết nối"}
        </span>
        <button type="button" ref={closeRef} className="rrugc-research-modal-close" onClick={onClose}
          aria-label="Close Research Intelligence" title="Đóng Research Intelligence">
          <ResearchGlyph name="close" size={19}/>
        </button>
      </header>
      <div className="rrugc-research-inbox-body">
        <div className="rrugc-research-overview" aria-label="Research overview">
          <div className="rrugc-research-stat"><span className="rrugc-research-stat-icon"><ResearchGlyph name="database"/></span><div><small>Nguồn thu thập</small><strong>{autoDiscover?.total_evidence ?? "—"}</strong></div></div>
          <div className="rrugc-research-stat"><span className="rrugc-research-stat-icon"><ResearchGlyph name="check"/></span><div><small>Keyword vào Stage 0</small><strong>{autoDiscover?.stage0_auto_keywords ?? "—"}</strong></div></div>
          <div className="rrugc-research-stat"><span className="rrugc-research-stat-icon"><ResearchGlyph name="clock"/></span><div><small>Đang chờ duyệt</small><strong>{autoDiscover?.pending_review ?? (loading ? "…" : items.length)}</strong></div></div>
          <div className="rrugc-research-stat"><span className="rrugc-research-stat-icon"><ResearchGlyph name="globe"/></span><div><small>Nền tảng</small><strong>{autoDiscover ? [autoDiscover.platforms.pinterest, autoDiscover.platforms.etsy, autoDiscover.platforms.amazon].filter(n => n > 0).length : "—"}<span className="rrugc-research-stat-suffix">/ 3</span></strong></div></div>
        </div>
      {unavailable ? <div className="rrugc-research-unavailable" role="status">
        <p>Research Intelligence chưa kích hoạt trên backend.</p>
        <button type="button" disabled={loading} onClick={() => void refresh()}>Thử kết nối lại</button>
      </div> : <>
        <section className="rrugc-discovery-status" aria-label="VPS Agent-Reach Auto Discover status">
          <div className="rrugc-discovery-status-heading">
            <span className="rrugc-discovery-status-icon"><ResearchGlyph name="radar" size={19}/></span>
            <div><strong>Agent-Reach Auto Discover</strong>
              <small>VPS · Hoạt động độc lập với Windows Scout Manager</small></div>
            <span className={"rrugc-discovery-state state-" + (autoDiscover?.state || "unknown")}>
              <span className="rrugc-research-state-dot" aria-hidden="true"/>
              {autoDiscover ? DISCOVERY_LABELS[autoDiscover.state] : "Chưa xác định"}
            </span>
          </div>
          <div className="rrugc-discovery-platforms">
            <span>Pinterest <strong>{autoDiscover?.platforms.pinterest ?? "—"}</strong></span>
            <span>Etsy <strong>{autoDiscover?.platforms.etsy ?? "—"}</strong></span>
            <span>Amazon <strong>{autoDiscover?.platforms.amazon ?? "—"}</strong></span>
            {autoDiscover?.last_seen_at && <small>Heartbeat: {new Date(autoDiscover.last_seen_at).toLocaleString()}</small>}
          </div>
          {discoveryErrorMessage(autoDiscover) && <p className="rrugc-discovery-warning" role="status"><ResearchGlyph name="warning"/>{discoveryErrorMessage(autoDiscover)}</p>}
          {autoDiscover?.state === "offline" && <p className="rrugc-discovery-warning" role="status"><ResearchGlyph name="warning"/>VPS chưa báo heartbeat: kiểm tra token Scout Agent.</p>}
          {autoDiscover?.connected && <button type="button" className="rrugc-research-vps-button" disabled={discoveryBusy}
            onClick={() => void toggleDiscoveryPause()}>
            <ResearchGlyph name={autoDiscover.state === "paused" ? "play" : "pause"}/>
            {autoDiscover.state === "paused" ? "Tiếp tục VPS" : "Tạm dừng VPS"}
          </button>}
        </section>
        {autoDiscover?.state === "not_configured" && <p className="rrugc-research-setup-note">VPS có thể thu thập công khai vào outbox riêng. Để đồng bộ vào CAM và bật điều khiển Pause/Resume, ghép một Scout Agent tên <strong>Agent-Reach Auto Discover</strong> và cấu hình token riêng trên VPS. Không dùng token Windows.</p>}
        <div className="rrugc-research-actions">
          <button type="button" className="rrugc-research-action-primary" onClick={() => setImportOpen(v => !v)}
            aria-expanded={importOpen}><ResearchGlyph name="upload"/>Nhập research JSON</button>
          <button type="button" disabled={busyId !== null} onClick={() => void applyApprovedToStage0()}>
            <ResearchGlyph name="sync"/>Đồng bộ Stage 0
          </button>
          <button type="button" disabled={busyId !== null} onClick={() => void previewExport()}>
            <ResearchGlyph name="list"/>Keyword đã duyệt
          </button>
          <button type="button" disabled={loading} onClick={() => void refresh()}>
            <ResearchGlyph name="refresh"/>Tải lại
          </button>
        </div>
        {importOpen && <div className="rrugc-research-import">
          <label htmlFor="rrugc-research-import-text">Research signals JSON (tối đa 50)</label>
          <textarea id="rrugc-research-import-text" rows={4} value={importText}
            onChange={event => setImportText(event.target.value)}
            placeholder={'[{"source":"etsy","channel":"listing","title":"Embroidered trucker hat","url":"https://www.etsy.com/listing/123456789"}]'} />
          <button type="button" disabled={busyId !== null || !importText.trim()}
            onClick={() => void importBatch()}>Nhập vào hàng chờ duyệt</button>
        </div>}
        {error && <p role="alert" className="rrugc-research-error">{error}</p>}
        {notice && <p role="status" className="rrugc-research-notice">{notice}</p>}
        {exportJson && <div className="rrugc-research-export">
          <label htmlFor="rrugc-research-export-text">Stage 0 export (chưa gửi)</label>
          <textarea id="rrugc-research-export-text" readOnly rows={4} value={exportJson} />
        </div>}
        <div className="rrugc-research-section-heading">
          <div><ResearchGlyph name="list" size={18}/><strong>Tín hiệu chờ duyệt</strong><span>{loading ? "…" : items.length}</span></div>
          <small>Các nguồn chưa xác minh · Chỉ duyệt keyword phù hợp</small>
        </div>
        {loading && <div role="status" className="rrugc-research-loading"><ResearchGlyph name="refresh"/>Đang tải tín hiệu...</div>}
        {!loading && !items.length && <div className="rrugc-research-empty"><ResearchGlyph name="search" size={26}/><strong>Chưa có tín hiệu chờ duyệt</strong><span>Kết quả mới từ Agent-Reach sẽ xuất hiện tại đây.</span></div>}
        <div className="rrugc-research-signal-list">
          {items.map(row => <article className="rrugc-research-signal" key={row.id}>
            <div className="rrugc-research-signal-main">
              <small>{row.channel} · {row.platform && row.platform !== "other" ? row.platform.charAt(0).toUpperCase() + row.platform.slice(1) : row.source} · Chưa xác minh</small>
              <strong>{row.title}</strong>
              {row.excerpt && <p>{row.excerpt}</p>}
              <a href={row.url} target="_blank" rel="noopener noreferrer"><ResearchGlyph name="external" size={14}/>Xem nguồn gốc</a>
            </div>
            <div className="rrugc-research-signal-review">
              <label htmlFor={`research-keyword-${row.id}`}>Keyword đề xuất</label>
              <input id={`research-keyword-${row.id}`} value={keywords[row.id] || ""}
                placeholder="VD: MADE TO WORSHIP"
                onChange={event => setKeywords(prev => ({ ...prev, [row.id]: event.target.value }))} />
              <div>
                <button type="button" disabled={busyId !== null || !(keywords[row.id] || "").trim()}
                  onClick={() => void decide(row, "approved")}><ResearchGlyph name="check" size={14}/>Duyệt</button>
                <button type="button" disabled={busyId !== null}
                  onClick={() => void decide(row, "rejected")}><ResearchGlyph name="close" size={14}/>Bỏ qua</button>
              </div>
            </div>
          </article>)}
        </div>
      </>}
      </div>
    </section>
  </div>, document.body);
}
