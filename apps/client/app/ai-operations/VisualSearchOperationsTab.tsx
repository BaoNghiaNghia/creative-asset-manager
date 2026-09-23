import type { VisualSearchCoverage, VisualSearchDiagnostics, VisualSearchSourceCoverage, VisualSearchSourceCoverageResponse } from "../../features/ai_operations";

type Props = {
  coverage: VisualSearchCoverage | null;
  sources: VisualSearchSourceCoverageResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  diagnostics: VisualSearchDiagnostics | null;
  diagnosticsLoading: boolean;
  diagnosticsError: string | null;
  onLoadDiagnostics: () => void;
};
type IconName = "visual" | "eligible" | "indexed" | "sync" | "queue" | "running" | "review" | "source";
const iconPaths: Record<IconName, string> = {
  visual: "M4 7h3l1.4-2h7.2L17 7h3v11H4V7z M12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  eligible: "M12 3l2.2 5.1 5.5.5-4.2 3.6 1.3 5.3-4.8-2.9-4.8 2.9 1.3-5.3-4.2-3.6 5.5-.5L12 3z",
  indexed: "M5 4h11l3 3v13H5V4zm4 9l2 2 4-4",
  sync: "M19 8a7 7 0 0 0-12-2L5 8m0-4v4h4m-4 8a7 7 0 0 0 12 2l2-2m0 4v-4h-4",
  queue: "M5 6h14M5 12h14M5 18h10",
  running: "M12 7v5l3 2M3 12a9 9 0 1 0 9-9",
  review: "M12 8v5m0 4h.01M5 4h14v16H5V4z",
  source: "M4 5h16v14H4V5zm3 4h10M7 13h6",
};
function VisualIcon({ name }: { name: IconName }) { return <svg className="visual-ops-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={iconPaths[name]} /></svg>; }
const formatNumber = (value: number | null | undefined) => value == null ? "Chưa rõ" : new Intl.NumberFormat("vi-VN").format(value);
const formatPercent = (value: number | null | undefined) => value == null ? "Chưa rõ" : `${Math.round(value * 100)}%`;

function SummaryMetric({ label, detail, value, icon, tone = "default" }: { label: string; detail: string; value: number | null | undefined; icon: IconName; tone?: "default" | "success" | "info" | "warning" | "attention"; }) {
  return <article className={`visual-ops-summary-metric ${tone}`}><div className="pipeline-metric-heading"><span className={`visual-ops-summary-icon ${tone}`}><VisualIcon name={icon} /></span><span>{label}</span></div><strong>{formatNumber(value)}</strong><small>{detail}</small></article>;
}
function ContextIcon({ name, tone = "default" }: { name: IconName; tone?: "default" | "success" | "info" }) { return <span className={`visual-ops-context-icon ${tone}`}><VisualIcon name={name} /></span>; }
function SourceRow({ source }: { source: VisualSearchSourceCoverage }) {
  const searchable = source.ratios.whole_resource_searchable;
  return <tr><td className="visual-ops-source-cell"><span className="visual-ops-source-icon"><VisualIcon name="source" /></span><div><b>{source.display_name || "Nguồn chưa đặt tên"}</b><small>{source.source_type}</small></div></td><td>{formatNumber(source.discovered_images)}</td><td>{formatNumber(source.imported_images)}</td><td>{formatNumber(source.visual_eligible)}</td><td><span className="pipeline-queue-count complete">{formatNumber(source.visual_indexed_current)}</span></td><td><span className="pipeline-queue-count waiting">{formatNumber(source.visual_index_missing)}</span></td><td><span className="pipeline-queue-count waiting">{formatNumber(source.visual_index_stale)}</span></td><td>{formatNumber(source.unsupported_images)}</td><td><span className={searchable == null ? "visual-ops-status unknown" : searchable >= 0.99 ? "visual-ops-status ready" : "visual-ops-status pending"}>{formatPercent(searchable)}</span></td></tr>;
}


function VisualSearchSkeleton() {
  return <div className="ops-content pipeline-content visual-ops visual-ops-skeleton" role="status" aria-live="polite" aria-label="Đang tải dữ liệu Visual Search">
    <header className="visual-ops-heading"><div className="visual-ops-heading-copy"><i className="visual-ops-skeleton-icon" /><div><i className="visual-ops-skeleton-line label" /><i className="visual-ops-skeleton-line title" /><i className="visual-ops-skeleton-line copy" /></div></div><i className="visual-ops-skeleton-pill" /></header>
    <section className="pipeline-summary visual-ops-summary" aria-hidden="true">{Array.from({ length: 5 }, (_, index) => <article className="visual-ops-skeleton-metric" key={index}><i className="visual-ops-skeleton-line metric-label" /><i className="visual-ops-skeleton-line metric-value" /><i className="visual-ops-skeleton-line metric-copy" /></article>)}</section>
    <div className="pipeline-context-row visual-ops-context-row" aria-hidden="true">{Array.from({ length: 3 }, (_, index) => <section className="visual-ops-skeleton-context" key={index}><i className="visual-ops-skeleton-line label" /><i className="visual-ops-skeleton-line context-title" /><i className="visual-ops-skeleton-line context-copy" /><div><i /><i /><i /></div></section>)}</div>
    <section className="pipeline-queue visual-ops-skeleton-table" aria-hidden="true"><header><div><i className="visual-ops-skeleton-line label" /><i className="visual-ops-skeleton-line table-title" /><i className="visual-ops-skeleton-line table-copy" /></div><i className="visual-ops-skeleton-pill" /></header>{Array.from({ length: 3 }, (_, index) => <div className="visual-ops-skeleton-row" key={index}><i /><i /><i /><i /><i /><i /></div>)}</section>
    <span className="sr-only">Đang tải dữ liệu Visual Search…</span>
  </div>;
}

export function VisualSearchOperationsTab({ coverage, sources, loading, error, onRetry, diagnostics, diagnosticsLoading, diagnosticsError, onLoadDiagnostics }: Props) {
  if (loading || (!coverage && !error)) return <VisualSearchSkeleton />;
  if (error) return <div className="visual-ops-state visual-ops-state-error"><div><b>Không thể tải trạng thái Visual Search.</b><span>{error}</span></div><button type="button" onClick={onRetry}>Thử lại</button></div>;
  if (!coverage) return <div className="visual-ops-state">Chưa có dữ liệu coverage cho Visual Search.</div>;

  const { totals, ratios } = coverage;
  const actionNeeded = (totals.visual_index_missing || 0) + (totals.visual_index_stale || 0);
  const indexAvailable = coverage.index_state === "available";
  const queueTotal = totals.visual_jobs_pending || 0;
  return <div className="ops-content pipeline-content visual-ops" aria-label="Visual Search pipeline">
    <header className="visual-ops-heading"><div className="visual-ops-heading-copy"><span className="visual-ops-heading-icon"><VisualIcon name="visual" /></span><div><small>VISUAL SEARCH</small><h2>Tiến trình lập chỉ mục hình ảnh</h2><p>Theo dõi ảnh nguồn, mức sẵn sàng tìm kiếm và phần việc cần đồng bộ.</p></div></div><div className="visual-ops-heading-actions"><button type="button" onClick={onLoadDiagnostics} disabled={diagnosticsLoading}>{diagnosticsLoading ? "Đang tải diagnostics…" : "Export diagnostics"}</button><span className={indexAvailable ? "visual-ops-index-state ready" : "visual-ops-index-state unavailable"}><i aria-hidden="true" />{indexAvailable ? "Elasticsearch sẵn sàng" : "Elasticsearch chưa khả dụng"}</span></div></header>
    {diagnosticsError && <div className="visual-ops-diagnostics-error" role="alert">{diagnosticsError}</div>}
    {diagnostics && <details className="visual-ops-diagnostics" open><summary>Visual Search diagnostics JSON</summary><pre>{JSON.stringify(diagnostics, null, 2)}</pre></details>}
    <section className="pipeline-summary visual-ops-summary" aria-label="Tóm tắt Visual Search"><SummaryMetric icon="eligible" label="Ảnh đủ điều kiện" value={totals.visual_eligible} detail="Có thể tạo embedding" /><SummaryMetric icon="indexed" label="Sẵn sàng tìm kiếm" value={totals.visual_indexed_current} detail={`${formatPercent(ratios.eligible_visual_coverage)} ảnh đủ điều kiện`} tone="success" /><SummaryMetric icon="running" label="Đang xử lý" value={totals.visual_jobs_processing} detail="Job đồng bộ đang chạy" tone="info" /><SummaryMetric icon="queue" label="Đang chờ xử lý" value={queueTotal} detail="Chờ bắt đầu hoặc thử lại" tone="warning" /><SummaryMetric icon="review" label="Cần xử lý" value={totals.visual_jobs_failed} detail="Job cần kiểm tra" tone="attention" /></section>
    <div className="pipeline-context-row visual-ops-context-row" role="group" aria-label="Tình trạng Visual Search">
      <section className="pipeline-scan-card visual-ops-corpus-card"><div className="pipeline-context-heading"><small>PHẠM VI LẬP CHỈ MỤC</small><div className="pipeline-context-title"><ContextIcon name="sync" /><h2>{indexAvailable ? "Đồng bộ corpus Visual Search" : "Chờ kết nối Elasticsearch"}</h2></div><p>{indexAvailable ? "Projection current được giữ nguyên; pipeline chỉ xử lý tài sản thiếu hoặc stale." : "Số liệu phụ thuộc Elasticsearch giữ trạng thái chưa rõ, không được hiểu là 0."}</p></div><dl><div><dt>Đã phát hiện</dt><dd>{formatNumber(totals.discovered_images)}</dd></div><div><dt>Đã nhập</dt><dd>{formatNumber(totals.imported_images)}</dd></div><div><dt>Cần đồng bộ</dt><dd>{indexAvailable ? formatNumber(actionNeeded) : "Chưa rõ"}</dd></div></dl></section>
      <section className="pipeline-active-job visual-ops-active-card" aria-live="polite"><div className="pipeline-context-heading"><small>HÀNG ĐỢI HIỆN TẠI</small><div className="pipeline-context-title"><ContextIcon name="running" tone="info" /><div className="pipeline-active-title"><span className={totals.visual_jobs_processing ? "pipeline-status-dot active" : "pipeline-status-dot"} aria-hidden="true" /><h2>{totals.visual_jobs_processing ? "Đang đồng bộ chỉ mục" : "Không có job đang chạy"}</h2></div></div><p>{totals.visual_jobs_processing ? "Worker đang xử lý các tài sản cần tạo hoặc làm mới projection." : "Worker sẽ nhận các job đang chờ khi có khả năng xử lý."}</p></div><dl><div><dt>Đang chạy</dt><dd>{formatNumber(totals.visual_jobs_processing)}</dd></div><div><dt>Đang chờ</dt><dd>{formatNumber(totals.visual_jobs_pending)}</dd></div><div><dt>Chờ xử lý</dt><dd>{formatNumber(totals.visual_jobs_pending)}</dd></div><div><dt>Cần kiểm tra</dt><dd>{formatNumber(totals.visual_jobs_failed)}</dd></div></dl></section>
      <section className="pipeline-progress-summary visual-ops-progress-card" aria-label="Mức sẵn sàng của tài sản"><div className="pipeline-context-heading"><small>MỨC SẴN SÀNG CỦA TÀI SẢN</small><div className="pipeline-context-title"><ContextIcon name="indexed" tone="success" /><h2>Giai đoạn đã xác thực</h2></div><p>Mỗi SourceAsset được tính độc lập để phản ánh đúng tài nguyên từ từng nguồn.</p></div><dl><div><dt>Phát hiện</dt><dd>{formatNumber(totals.discovered_images)}</dd></div><div><dt>Đã nhập</dt><dd>{formatNumber(totals.imported_images)}</dd></div><div><dt>Đủ điều kiện</dt><dd>{formatNumber(totals.visual_eligible)}</dd></div><div><dt>Sẵn sàng</dt><dd>{formatNumber(totals.visual_indexed_current)}</dd></div></dl></section>
    </div>
    <section className="pipeline-queue visual-ops-sources" aria-label="Độ phủ Visual Search theo nguồn"><header><div><small>NGUỒN DỮ LIỆU</small><h2>Độ phủ theo từng nguồn</h2><p>Đối chiếu ảnh từ nguồn, khả năng lập chỉ mục và phần còn cần xử lý.</p></div><time dateTime={coverage.generated_at}>Cập nhật {new Date(coverage.generated_at).toLocaleString("vi-VN")}</time></header>{!sources?.sources.length ? <p className="pipeline-queue-empty">Chưa có nguồn ảnh nào trong tenant hiện tại.</p> : <div className="ops-table-scroll"><table className="ops-data-table visual-ops-source-table"><caption className="sr-only">Độ phủ Visual Search theo nguồn dữ liệu</caption><thead><tr><th>Nguồn</th><th>Phát hiện</th><th>Đã nhập</th><th>Đủ điều kiện</th><th>Current</th><th>Thiếu</th><th>Stale</th><th>Không hỗ trợ</th><th>Sẵn sàng</th></tr></thead><tbody>{sources.sources.map(source => <SourceRow key={source.source_id} source={source} />)}</tbody></table></div>}</section>
  </div>;
}
