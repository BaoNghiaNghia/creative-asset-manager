import {useCallback, useEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";
import {spyFetch, type Listing, type TikTokListingDraft, type TikTokListingView} from "./api";

const LABELS: Record<string, string> = {
  not_queued: "Chưa tạo", pending: "Đang chờ", retry: "Chờ retry",
  processing: "Đang tạo", needs_fact_check: "Cần duyệt",
  failed: "Tạo lỗi", cancelled: "Đã hủy",
};
const ACTIVE_STATUSES = ["pending", "retry", "processing"];

export function canQueueTikTokDraft(status: string | null | undefined): boolean {
  return !status || status === "not_queued" || status === "failed";
}

export function formatTikTokDraft(draft: TikTokListingDraft): string {
  return [
    "PRODUCT TITLE", draft.title,
    "PRODUCT DESCRIPTION", draft.description,
    "PRODUCT HIGHLIGHTS", ...draft.highlights.map((text, index) => (index + 1) + ". " + text),
    "SEARCH TERMS", draft.search_terms.join(", "),
  ].join("\n\n");
}

function CopyField({label, value, count}: {label: string; value: string; count?: string}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  useEffect(() => { setCopied(false); setCopyError(false); }, [value]);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true); setCopyError(false);
    } catch {
      setCopied(false); setCopyError(true);
    }
  };
  return <section className="etsy-tiktok-copy-field">
    <div className="etsy-tiktok-copy-title"><strong>{label}</strong>
      {count && <span>{count}</span>}
      <button type="button" onClick={() => void onCopy()} disabled={!value}
        aria-label={"Copy " + label}>{copied ? "Đã copy" : "Copy"}</button>
    </div>
    <div className="etsy-tiktok-copy-content">{value || "—"}</div>
    {copyError && <p role="alert" className="etsy-tiktok-copy-error">
      Không thể sao chép tự động. Hãy chọn và copy văn bản.
    </p>}
  </section>;
}

export function TikTokListingCell({item, canManage, optimizerEnabled, onQueued}: {
  item: Listing; canManage: boolean; optimizerEnabled: boolean; onQueued: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TikTokListingView | null>(null);
  const [loading, setLoading] = useState(false);
  const [queueing, setQueueing] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [showPrevious, setShowPrevious] = useState(false);
  const [researching, setResearching] = useState(false);
  const [error, setError] = useState("");
  const [copiedAll, setCopiedAll] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const queuedInDialog = useRef(false);

  const listing = item.tiktokListing;
  const status = listing?.status || "not_queued";
  const isActive = ACTIVE_STATUSES.includes(status);
  const canGenerate = canManage && optimizerEnabled && canQueueTikTokDraft(status);
  const loadDetail = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const next = await spyFetch<TikTokListingView>(
        "/listings/" + encodeURIComponent(item.listingKey) + "/tiktok-listing");
      setDetail(next);
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể tải bản nháp");
      return null;
    } finally {
      setLoading(false);
    }
  }, [item.listingKey]);
  const closeDialog = () => {
    setOpen(false);
    if (queuedInDialog.current) {
      queuedInDialog.current = false;
      onQueued();
    }
  };

  useEffect(() => {
    if (!open) return;
    void loadDetail();
    const previousOverflow = document.body.style.overflow;
    const restoreFocus = document.activeElement instanceof HTMLElement &&
      document.activeElement !== document.body
      ? document.activeElement : triggerRef.current;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDialog();
      } else if (event.key === "Tab") {
        const buttons = Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>(
          "button:not([disabled])") ?? []);
        if (!buttons.length) return;
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      restoreFocus?.focus();
    };
    // Opening/closing is the focus boundary; callback identities must not
    // repeatedly steal focus while the worker status refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, loadDetail]);

  const enqueue = async () => {
    const currentStatus = (open && detail?.status) || status;
    if (!canManage || !optimizerEnabled || queueing || !canQueueTikTokDraft(currentStatus)) return;
    setQueueing(true); setError("");
    try {
      await spyFetch(
        "/listings/" + encodeURIComponent(item.listingKey) +
        (currentStatus === "failed" ? "/tiktok-listing/retry" : "/tiktok-listing/queue"),
        {method: "POST"});
      if (open) {
        // Refresh the parent row only when the dialog closes; otherwise a
        // table fetch unmounts the dialog before the result/status can be read.
        queuedInDialog.current = true;
        await loadDetail();
      } else {
        onQueued();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể thêm vào hàng đợi");
      if (!open) setOpen(true);
    } finally {
      setQueueing(false);
    }
  };

  useEffect(() => {
    if (!open || !detail || !ACTIVE_STATUSES.includes(detail.status)) return;
    const timer = window.setTimeout(() => void loadDetail(), 12000);
    return () => window.clearTimeout(timer);
  }, [open, detail, loadDetail]);

  const isShowingPrevious = Boolean(detail?.previous_draft &&
    (showPrevious || !detail?.result));
  const result = isShowingPrevious ? detail?.previous_draft : detail?.result;
  const metrics = result?.metrics;
  const evidence = result?.ytrends_evidence;
  const ytrendsStatus = detail?.result?.ytrends_status ??
    (detail && ACTIVE_STATUSES.includes(detail.status) ? "pending" :
      result?.ytrends_status ?? "unavailable");
  const detailStatus = detail?.status || status;
  const regenerate = async () => {
    if (!canManage || !optimizerEnabled || !detail?.can_regenerate ||
        detailStatus !== "needs_fact_check" || regenerating || queueing) return;
    setRegenerating(true); setError("");
    try {
      await spyFetch<TikTokListingView>(
        "/listings/" + encodeURIComponent(item.listingKey) + "/tiktok-listing/regenerate",
        {method: "POST"});
      queuedInDialog.current = true;
      setShowPrevious(false);
      await loadDetail();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể tạo lại bản nháp");
    } finally {
      setRegenerating(false);
    }
  };
  const refreshResearch = async () => {
    if (!canManage || !result || detailStatus !== "needs_fact_check" || researching) return;
    setResearching(true); setError("");
    try {
      const next = await spyFetch<TikTokListingView>(
        "/listings/" + encodeURIComponent(item.listingKey) + "/tiktok-listing/ytrends/refresh",
        {method: "POST"});
      setDetail(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "YTrends chưa sẵn sàng");
    } finally {
      setResearching(false);
    }
  };
  const copyAll = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(formatTikTokDraft(result));
      setCopiedAll(true); setError("");
    } catch {
      setError("Không thể sao chép tự động. Hãy copy từng trường.");
    }
  };

  return <td className="etsy-tiktok-listing-cell">
    <div className="etsy-tiktok-cell-stack">
      <button type="button" className="etsy-tiktok-listing-open" ref={triggerRef}
        title="TikTok Shop US · Bản nháp cần duyệt, không tự đăng"
        aria-label={"Xem TikTok SEO cho " + item.pageTitle}
        onClick={() => setOpen(true)}>
        <span className={"etsy-tiktok-state is-" + status}>
          {LABELS[status] || status}
        </span>
        {listing?.title_preview
          ? <strong className="etsy-tiktok-title-preview">{listing.title_preview}</strong>
          : <span className="etsy-tiktok-empty-preview">{isActive ? "Worker đang xử lý" :
            status === "failed" ? "Xem nguyên nhân lỗi" : "Title · Description · Highlights · Terms"}</span>}
        <span className="etsy-tiktok-open-hint">
          {status === "needs_fact_check" ? "Xem & copy bản nháp" : "Xem chi tiết"}
        </span>
      </button>
      {canGenerate && <button type="button" className="etsy-tiktok-queue-action"
        disabled={queueing} onClick={() => void enqueue()}
        aria-label={(status === "failed" ? "Retry TikTok SEO cho " : "Tạo TikTok SEO cho ") + item.pageTitle}>
        {queueing ? "Đang gửi…" : status === "failed" ? "Thử lại" : "Tạo SEO"}
      </button>}
      {!optimizerEnabled && <span className="etsy-tiktok-offline">Worker đang tắt</span>}
    </div>
    {open && typeof document !== "undefined" && createPortal(
      <div className="etsy-tiktok-dialog-backdrop" onClick={closeDialog}>
        <section className="etsy-tiktok-dialog" role="dialog" aria-modal="true"
          aria-label={"TikTok listing for " + item.pageTitle} ref={dialogRef}
          onClick={event => event.stopPropagation()}>
          <div className="etsy-tiktok-dialog-header">
            <div>
              <strong>TikTok Shop US · Listing Optimizer</strong>
              <small title={item.pageTitle}>{item.pageTitle}</small>
            </div>
            <button type="button" ref={closeRef} aria-label="Close TikTok listing"
              onClick={closeDialog}>×</button>
          </div>
          <div className="etsy-tiktok-dialog-body">
            <div className="etsy-tiktok-skill-meta">
              <span className={"etsy-tiktok-state is-" + detailStatus}>
                {LABELS[detailStatus] || detailStatus}
              </span>
              <span>Manual · Draft only</span>
              <span>YTrends · Etsy: {ytrendsStatus === "connected" ? "Có dữ liệu" :
                ytrendsStatus === "no_data" ? "Không thấy dữ liệu phù hợp" :
                ytrendsStatus === "disabled" ? "Đang tắt" :
                ytrendsStatus === "pending" ? "Đang chờ" : "Chưa có dữ liệu"}</span>
            </div>
            {loading && <p role="status" className="etsy-tiktok-loading">Đang cập nhật trạng thái…</p>}
            {detail?.previous_draft && detail?.result && <button type="button"
              className="etsy-tiktok-history-toggle"
              onClick={() => setShowPrevious(value => !value)}>
              {showPrevious ? "Xem bản mới" : "Xem bản nháp cũ"}
            </button>}
            {isShowingPrevious && <p className="etsy-tiktok-history-note">
              Đang xem bản nháp trước. {ACTIVE_STATUSES.includes(detailStatus)
                ? "Phiên bản mới đang chạy YTrends và Gemini; dữ liệu cũ được giữ nguyên."
                : "Bạn có thể đối chiếu với bản nháp mới."}
            </p>}
            {result ? <>
              <p className="etsy-tiktok-qa-note">Cần kiểm tra thông tin sản phẩm, thương hiệu
                và quyền sử dụng trước khi đăng. YTrends chỉ cung cấp tham khảo thị trường Etsy;
                không chứng minh search volume hay hiệu quả SEO trên TikTok Shop.</p>
              {evidence && <section className="etsy-tiktok-research" aria-label="Nghiên cứu Etsy từ YTrends">
                <strong>YTrends · Dữ liệu thị trường Etsy</strong>
                <span>Keyword: {evidence.query || "—"}</span>
                <span>Snapshot: {evidence.as_of_date || "Chưa có"}
                  {evidence.median_data_age_days != null
                    ? " · Tuổi dữ liệu trung vị " + evidence.median_data_age_days + " ngày" : ""}</span>
                {evidence.status === "connected" ? <span>
                  {evidence.total_listings != null ? evidence.total_listings + " listings" : "Listings: không rõ"}
                  {" · "}
                  {evidence.total_sellers != null ? evidence.total_sellers + " sellers" : "Sellers: không rõ"}
                  {evidence.competition_level ? " · Cạnh tranh: " + evidence.competition_level : ""}
                </span> : <span>{evidence.status === "no_data"
                  ? "Không có listing phù hợp trong dữ liệu YTrends."
                  : "Không có dữ liệu xác minh cho lần tạo này."}</span>}
                <small>Dữ liệu từ tập Etsy được lập chỉ mục, không phải số liệu TikTok Shop.</small>
              </section>}
              <CopyField label="Product Title" value={result.title}
                count={metrics ? metrics.title_characters + " / 200 ký tự" : undefined} />
              <CopyField label="Product Description" value={result.description}
                count={metrics ? metrics.description_words + " words · " +
                  metrics.description_characters + " ký tự" : undefined} />
              <CopyField label="Product Highlights"
                value={result.highlights.map((v, i) => (i + 1) + ". " + v).join("\n")}
                count={metrics ? metrics.highlights_count + " mục · " +
                  metrics.highlights_characters_total + " / 1500" : undefined} />
              <CopyField label="Search Terms" value={result.search_terms.join(", ")}
                count={metrics ? metrics.search_terms_count + " cụm · " +
                  metrics.search_terms_joined_characters + " / 250" : undefined} />
              {result.warnings?.length > 0 && <div className="etsy-tiktok-warnings">
                <strong>Lưu ý QA</strong>
                <ul>{result.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
              </div>}
              <p className="etsy-tiktok-copy-notice">Bản nháp nội bộ; không tự đăng TikTok Shop.
                Xác minh artwork, trademark, loại áo, size, care và vận chuyển.</p>
            </> : !loading && <p className="etsy-tiktok-empty-state">
              {ACTIVE_STATUSES.includes(detailStatus)
                ? "Đang xử lý bản nháp. Trạng thái sẽ được cập nhật tự động."
                : "Chưa có bản nháp. Chọn Tạo listing để đưa vào hàng đợi Gemini."}
            </p>}
            {detail?.error_code && <p className="etsy-tiktok-copy-notice">
              Job error: {detail.error_code}. Retry thủ công tối đa 2 lần.
            </p>}
            {error && <p role="alert" className="etsy-error">{error}</p>}
          </div>
          <div className="etsy-tiktok-dialog-actions">
            {result && <button type="button" className="etsy-tiktok-copy-all"
              onClick={() => void copyAll()}>{copiedAll ? "Đã copy tất cả" : "Copy tất cả"}</button>}
            {canManage && detail?.enabled && canQueueTikTokDraft(detailStatus) &&
              <button type="button" className="etsy-tiktok-dialog-run" disabled={queueing || loading}
                onClick={() => void enqueue()}>{queueing ? "Đang gửi…" :
                  detailStatus === "failed" ? "Retry failed draft" : "Run listing skill"}</button>}
            {canManage && detail?.enabled && detail.can_regenerate &&
              detailStatus === "needs_fact_check" &&
              <button type="button" className="etsy-tiktok-dialog-run"
                disabled={regenerating || researching || queueing || loading}
                onClick={() => void regenerate()}>
                {regenerating ? "Đang tạo job…" : "Tạo lại với YTrends"}
              </button>}
            {canManage && detail?.enabled && detail?.result && detailStatus === "needs_fact_check" &&
              <button type="button" disabled={researching || regenerating || loading}
                onClick={() => void refreshResearch()}>
                {researching ? "Đang lấy YTrends…" : "Cập nhật YTrends"}
              </button>}
            <button type="button" onClick={() => void loadDetail()} disabled={loading}>Refresh</button>
            <button type="button" onClick={closeDialog}>Close</button>
          </div>
        </section>
      </div>, document.body)}
  </td>;
}
