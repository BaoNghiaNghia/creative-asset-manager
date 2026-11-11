import {useCallback, useEffect, useState} from "react";
import {createPortal} from "react-dom";
import {spyFetch, type Listing, type TikTokListingView} from "./api";

const LABELS: Record<string, string> = {
  not_queued: "Chưa chạy", pending: "Queued", retry: "Retry",
  processing: "Generating", needs_fact_check: "Review copy",
  failed: "Failed", cancelled: "Cancelled",
};

function CopyField({label, value, count}: {label: string; value: string; count?: string}) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch { setCopied(false); }
  };
  return <section className="etsy-tiktok-copy-field">
    <div className="etsy-tiktok-copy-title"><strong>{label}</strong>
      <span>{count || ""}</span>
      <button type="button" onClick={onCopy} disabled={!value}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
    <div className="etsy-tiktok-copy-content">{value || "—"}</div>
  </section>;
}

export function TikTokListingCell({item, canManage, onQueued}: {
  item: Listing; canManage: boolean; onQueued: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TikTokListingView | null>(null);
  const [loading, setLoading] = useState(false);
  const [queueing, setQueueing] = useState(false);
  const [error, setError] = useState("");
  const listing = item.tiktokListing;
  const status = listing?.status || "not_queued";
  const loadDetail = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const next = await spyFetch<TikTokListingView>(
        "/listings/" + encodeURIComponent(item.listingKey) + "/tiktok-listing");
      setDetail(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load draft");
    } finally {
      setLoading(false);
    }
  }, [item.listingKey]);
  useEffect(() => {
    if (!open) return;
    void loadDetail();
    const onKey = (event: KeyboardEvent) => {if (event.key === "Escape") setOpen(false);};
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, loadDetail]);
  const enqueue = async () => {
    setQueueing(true); setError("");
    try {
      await spyFetch("/listings/" + encodeURIComponent(item.listingKey) +
        (detail?.status === "failed" ? "/tiktok-listing/retry" : "/tiktok-listing/queue"),
        {method: "POST"});
      await loadDetail();
      onQueued();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to enqueue skill");
    } finally {setQueueing(false);}
  };
  useEffect(() => {
    if (!open || !detail || !["pending", "retry", "processing"].includes(detail.status)) return;
    const timer = window.setTimeout(() => void loadDetail(), 12000);
    return () => window.clearTimeout(timer);
  }, [open, detail, loadDetail]);
  const result = detail?.result;
  const metrics = result?.metrics;
  return <td className="etsy-tiktok-listing-cell">
    <button type="button" className="etsy-tiktok-listing-open"
      title="TikTok Shop US listing draft · never auto-published"
      aria-label={"TikTok listing: " + item.pageTitle}
      onClick={() => setOpen(true)}>
      <strong>{LABELS[status] || status}</strong>
      {listing?.title_preview && <small>{listing.title_preview}</small>}
      <span>{status === "needs_fact_check" ? "Title · Description · 5 Highlights · Terms"
        : "TikTok Shop US"}</span>
    </button>
    {open && typeof document !== "undefined" && createPortal(
      <div className="etsy-tiktok-dialog-backdrop" onClick={() => setOpen(false)}>
        <section className="etsy-tiktok-dialog" role="dialog" aria-modal="true"
          aria-label={"TikTok listing for " + item.pageTitle}
          onClick={event => event.stopPropagation()}>
          <div className="etsy-tiktok-dialog-header">
            <div>
              <strong>TikTok Shop US · Listing Optimizer</strong>
              <small>{item.pageTitle}</small>
            </div>
            <button type="button" aria-label="Close TikTok listing" onClick={() => setOpen(false)}>×</button>
          </div>
          <div className="etsy-tiktok-dialog-body">
            {loading ? <p role="status">Loading listing draft…</p> : <>
              <div className="etsy-tiktok-skill-meta">
                <span>Status: {LABELS[detail?.status || status] || status}</span>
                <span>YTrends MCP: {detail?.ytrends_status === "unavailable" ?
                  "Không khả dụng · keyword chưa xác minh" : detail?.ytrends_status || "Không khả dụng"}</span>
                <span>QA: {result?.qa_status === "needs_fact_check" ?
                  "Cần kiểm tra thông tin sản phẩm / bản quyền" : "Chưa có kết quả"}</span>
              </div>
              {result ? <>
                <CopyField label="Product Title" value={result.title}
                  count={metrics?.title_characters + " / 200 ký tự"} />
                <CopyField label="Product Description" value={result.description}
                  count={metrics?.description_words + " words · " +
                    metrics?.description_characters + " ký tự"} />
                <CopyField label="Product Highlights"
                  value={result.highlights.map((v, i) => (i+1) + ". " + v).join("\n")}
                  count={metrics?.highlights_count + " items · " +
                    metrics?.highlights_characters_total + " / 1500"} />
                <CopyField label="Search Terms"
                  value={result.search_terms.join(", ")}
                  count={metrics?.search_terms_count + " phrases · " +
                    metrics?.search_terms_characters_total + " / 250"} />
                <p className="etsy-tiktok-copy-notice">Draft only. Không công bố lên TikTok Shop.
                  Kiểm tra artwork, trademark, loại áo, size, care và nguồn giao hàng
                  trước khi sử dụng.</p>
              </> : <p>Chưa có bản nháp listing. Khi worker chạy xong, kết quả sẽ hiện ở đây.</p>}
              {detail?.error_code && <p className="etsy-tiktok-copy-notice">
                Job error: {detail.error_code}. Có thể thử lại tối đa 2 lần thủ công.
              </p>}
              {error && <p role="alert" className="etsy-error">{error}</p>}
            </>}
          </div>
          <div className="etsy-tiktok-dialog-actions">
            {!result && canManage && detail?.enabled && <button type="button"
              disabled={queueing || loading || ["pending", "retry", "processing"].includes(detail?.status || "")}
              onClick={enqueue}>{queueing ? "Queueing…" : detail?.status === "failed"
                ? "Retry failed draft" : "Run listing skill"}</button>}
            <button type="button" onClick={() => void loadDetail()} disabled={loading}>Refresh</button>
            <button type="button" onClick={() => setOpen(false)}>Close</button>
          </div>
        </section>
      </div>, document.body)}
  </td>;
}