import { useCallback, useEffect, useState } from "react";
import "./ResearchInboxPanel.css";

export type ResearchSignal = {
  id: string; source: string; platform?: string; channel: string; title: string; excerpt: string;
  url: string; status: string; keyword: string | null; created_at: string;
};

export type AutoDiscoverStatus = {
  state: "not_configured" | "paused" | "ready" | "busy" | "offline" | "error";
  connected: boolean; server: string; source: string; last_seen_at: string | null;
  last_error_code: string | null; total_evidence: number; pending_review: number;
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

export function ResearchInboxPanel({ onKeywordApproved }: { onKeywordApproved?: () => void } = {}) {
  const [open, setOpen] = useState(false);
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
    const controller = new AbortController();
    void researchRequest<AutoDiscoverStatus>("/auto-discover/status", { signal: controller.signal })
      .then(status => { if (!controller.signal.aborted) setAutoDiscover(status); })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

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

  return <section className="rrugc-research-inbox" aria-label="Research Intelligence">
    <header className="rrugc-research-inbox-head">
      <div>
        <span className="rrugc-research-eyebrow">Agent-Reach · Research Intelligence</span>
        <h3>Research Inbox <span aria-hidden="true">·</span> GatorHats Products</h3>
        <p>Tín hiệu đa nguồn chờ duyệt · Duyệt keyword để thêm vào Stage 0; không tự gọi AEBrowse.</p>
        {autoDiscover?.state === "not_configured" && <p role="status">VPS chưa ghép Scout Agent: kết quả Tavily hiện chưa thể chuyển vào CAM và Stage 0.</p>}
        {autoDiscover?.state === "offline" && <p role="status">VPS chưa báo heartbeat: kiểm tra token Scout Agent để đồng bộ các kết quả đã tìm.</p>}
      </div>
      <button type="button" className="rrugc-research-expand" aria-expanded={open}
        onClick={() => setOpen(v => !v)}>
        {open ? "Thu gọn" : "Xem Research Inbox"}
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
    </header>
    {open && <div className="rrugc-research-inbox-body">
      {unavailable ? <div className="rrugc-research-unavailable" role="status">
        <p>Research Intelligence chưa kích hoạt trên backend.</p>
        <button type="button" disabled={loading} onClick={() => void refresh()}>Thử kết nối lại</button>
      </div> : <>
        <div className="rrugc-discovery-status" role="status" aria-label="VPS Agent-Reach Auto Discover status">
          <div>
            <strong>Agent-Reach Auto Discover · VPS</strong>
            <small>Hoạt động độc lập với Windows Scout Manager · chỉ tìm kiếm web công khai</small>
          </div>
          <span className={"rrugc-discovery-state state-" + (autoDiscover?.state || "unknown")}>
            {autoDiscover ? DISCOVERY_LABELS[autoDiscover.state] : "Chưa xác định"}
          </span>
          {autoDiscover && <small>{autoDiscover.total_evidence} nguồn · {autoDiscover.pending_review} chờ duyệt · Etsy {autoDiscover.platforms.etsy} / Amazon {autoDiscover.platforms.amazon} / Pinterest {autoDiscover.platforms.pinterest}</small>}
          {discoveryErrorMessage(autoDiscover) && <small>{discoveryErrorMessage(autoDiscover)}</small>}
          {autoDiscover?.connected && <button type="button" disabled={discoveryBusy}
            onClick={() => void toggleDiscoveryPause()}>
            {autoDiscover.state === "paused" ? "Tiếp tục VPS" : "Tạm dừng VPS"}
          </button>}
        </div>
        {autoDiscover?.state === "not_configured" && <p className="rrugc-research-setup-note">VPS có thể thu thập công khai vào outbox riêng. Để đồng bộ vào CAM và bật điều khiển Pause/Resume, ghép một Scout Agent tên <strong>Agent-Reach Auto Discover</strong> và cấu hình token riêng trên VPS. Không dùng token Windows.</p>}
        <div className="rrugc-research-actions">
          <button type="button" onClick={() => setImportOpen(v => !v)}
            aria-expanded={importOpen}>Nhập research JSON</button>
          <button type="button" disabled={busyId !== null} onClick={() => void applyApprovedToStage0()}>
            Đồng bộ keyword đã duyệt vào Stage 0
          </button>
          <button type="button" disabled={busyId !== null} onClick={() => void previewExport()}>
            Xem keyword đã duyệt
          </button>
          <button type="button" disabled={loading} onClick={() => void refresh()}>
            Tải lại
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
        {loading && <p role="status" className="rrugc-research-loading">Đang tải tín hiệu...</p>}
        {!loading && !items.length && <p className="rrugc-research-empty">Chưa có tín hiệu chờ duyệt.</p>}
        <div className="rrugc-research-signal-list">
          {items.map(row => <article className="rrugc-research-signal" key={row.id}>
            <div className="rrugc-research-signal-main">
              <small>{row.channel} · {row.platform && row.platform !== "other" ? row.platform.charAt(0).toUpperCase() + row.platform.slice(1) : row.source} · Chưa xác minh</small>
              <strong>{row.title}</strong>
              {row.excerpt && <p>{row.excerpt}</p>}
              <a href={row.url} target="_blank" rel="noopener noreferrer">Xem nguồn gốc <span aria-hidden="true">↗</span></a>
            </div>
            <div className="rrugc-research-signal-review">
              <label htmlFor={`research-keyword-${row.id}`}>Keyword đề xuất</label>
              <input id={`research-keyword-${row.id}`} value={keywords[row.id] || ""}
                placeholder="VD: MADE TO WORSHIP"
                onChange={event => setKeywords(prev => ({ ...prev, [row.id]: event.target.value }))} />
              <div>
                <button type="button" disabled={busyId !== null || !(keywords[row.id] || "").trim()}
                  onClick={() => void decide(row, "approved")}>Duyệt</button>
                <button type="button" disabled={busyId !== null}
                  onClick={() => void decide(row, "rejected")}>Bỏ qua</button>
              </div>
            </div>
          </article>)}
        </div>
      </>}
    </div>}
  </section>;
}
