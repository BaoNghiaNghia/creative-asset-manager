import { useCallback, useEffect, useState } from "react";
import "./ResearchInboxPanel.css";

export type ResearchSignal = {
  id: string; source: string; channel: string; title: string; excerpt: string;
  url: string; status: string; keyword: string | null; created_at: string;
};

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

export function parseResearchImport(value: string): Array<Record<string, unknown>> {
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed) || !parsed.length || parsed.length > 50) {
    throw new Error("Tối đa 50 research signals cho mỗi lần nhập.");
  }
  for (const signal of parsed) {
    if (!signal || typeof signal !== "object" || Array.isArray(signal)
      || !["rss", "github", "youtube", "web"].includes((signal as Record<string, unknown>).source as string)
      || typeof (signal as Record<string, unknown>).title !== "string"
      || !String((signal as Record<string, unknown>).title).trim()
      || String((signal as Record<string, unknown>).title).length > 240
      || !isPublicHttpsUrl((signal as Record<string, unknown>).url)) {
      throw new Error("Dữ liệu research phải có source, title và URL HTTPS hợp lệ.");
    }
  }
  return parsed as Array<Record<string, unknown>>;
}

export function ResearchInboxPanel() {
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

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const result = await researchRequest<{ items: ResearchSignal[] }>("/signals?status=pending&limit=25",
        signal ? { signal } : undefined);
      if (signal?.aborted) return;
      setItems(result.items);
      setUnavailable(false);
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
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [open, refresh]);

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
        ? "Đã duyệt keyword. Chưa tự gọi AEBrowse." : "Đã bỏ tín hiệu khỏi hàng chờ.");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không cập nhật được tín hiệu.");
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
        <p>Tín hiệu thị trường chờ duyệt. Không tự sinh keyword hay chạy AEBrowse.</p>
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
        <div className="rrugc-research-actions">
          <button type="button" onClick={() => setImportOpen(v => !v)}
            aria-expanded={importOpen}>Nhập research JSON</button>
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
            placeholder={'[{"source":"rss","channel":"hats","title":"...","url":"https://...","excerpt":"..."}]'} />
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
              <small>{row.channel} · {row.source}</small>
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
