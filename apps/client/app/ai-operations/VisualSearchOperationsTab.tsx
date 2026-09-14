import type {
  VisualSearchCoverage,
  VisualSearchSourceCoverage,
  VisualSearchSourceCoverageResponse,
} from "../../features/ai_operations";

type Props = {
  coverage: VisualSearchCoverage | null;
  sources: VisualSearchSourceCoverageResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
};

const formatNumber = (value: number | null | undefined) => value == null ? "Chưa rõ" : new Intl.NumberFormat("vi-VN").format(value);
const formatPercent = (value: number | null | undefined) => value == null ? "Chưa rõ" : `${Math.round(value * 100)}%`;

function PipelineStage({ label, detail, value, tone = "default" }: {
  label: string; detail: string; value: number | null | undefined; tone?: "default" | "success" | "attention";
}) {
  return <article className={`visual-ops-stage visual-ops-stage-${tone}`}>
    <span className="visual-ops-stage-dot" aria-hidden="true" />
    <small>{label}</small>
    <strong>{formatNumber(value)}</strong>
    <p>{detail}</p>
  </article>;
}

function SourceRow({ source }: { source: VisualSearchSourceCoverage }) {
  const searchable = source.ratios.whole_resource_searchable;
  return <tr>
    <td><b>{source.display_name || "Nguồn chưa đặt tên"}</b><small>{source.source_type}</small></td>
    <td>{formatNumber(source.discovered_images)}</td>
    <td>{formatNumber(source.imported_images)}</td>
    <td>{formatNumber(source.visual_eligible)}</td>
    <td className="visual-ops-current">{formatNumber(source.visual_indexed_current)}</td>
    <td className="visual-ops-attention">{formatNumber(source.visual_index_missing)}</td>
    <td className="visual-ops-attention">{formatNumber(source.visual_index_stale)}</td>
    <td>{formatNumber(source.unsupported_images)}</td>
    <td><span className={searchable == null ? "visual-ops-status unknown" : searchable >= 0.99 ? "visual-ops-status ready" : "visual-ops-status pending"}>{formatPercent(searchable)}</span></td>
  </tr>;
}

export function VisualSearchOperationsTab({ coverage, sources, loading, error, onRetry }: Props) {
  if (loading || (!coverage && !error)) return <div className="visual-ops-state">Đang tải dữ liệu Visual Search…</div>;
  if (error) return <div className="visual-ops-state visual-ops-state-error"><div><b>Không thể tải trạng thái Visual Search.</b><span>{error}</span></div><button type="button" onClick={onRetry}>Thử lại</button></div>;
  if (!coverage) return <div className="visual-ops-state">Chưa có dữ liệu coverage cho Visual Search.</div>;

  const { totals, ratios } = coverage;
  const actionNeeded = (totals.visual_index_missing || 0) + (totals.visual_index_stale || 0);
  const indexAvailable = coverage.index_state === "available";
  return <div className="visual-ops" aria-label="Visual Search pipeline">
    <header className="visual-ops-header">
      <div><small>VISUAL SEARCH</small><h2>Tiến trình lập chỉ mục hình ảnh</h2><p>Theo dõi toàn bộ vòng đời từ ảnh được phát hiện đến kết quả sẵn sàng tìm kiếm tương tự.</p></div>
      <div className={indexAvailable ? "visual-ops-index-state ready" : "visual-ops-index-state unavailable"}><span aria-hidden="true" />{indexAvailable ? "Elasticsearch sẵn sàng" : "Elasticsearch chưa khả dụng"}</div>
    </header>

    <section className="visual-ops-pipeline" aria-label="Visual Search lifecycle">
      <PipelineStage label="1. Đã phát hiện" detail="Ảnh còn hoạt động từ nguồn" value={totals.discovered_images} />
      <PipelineStage label="2. Đã nhập" detail="Đã liên kết vào CAM" value={totals.imported_images} />
      <PipelineStage label="3. Đủ điều kiện" detail="Có thể tạo embedding" value={totals.visual_eligible} />
      <PipelineStage label="4. Đã lập chỉ mục" detail="Projection hiện hành" value={totals.visual_indexed_current} tone="success" />
      <PipelineStage label="5. Cần đồng bộ" detail="Thiếu hoặc stale" value={actionNeeded} tone="attention" />
    </section>

    <section className="visual-ops-summary-grid" aria-label="Visual Search metrics">
      <article><small>Độ phủ import</small><strong>{formatPercent(ratios.import_coverage)}</strong><span>Trong ảnh đã phát hiện</span></article>
      <article><small>Sẵn sàng Visual Search</small><strong>{formatPercent(ratios.eligible_visual_coverage)}</strong><span>Trong ảnh đủ điều kiện</span></article>
      <article><small>Không hỗ trợ</small><strong>{formatNumber(totals.unsupported_images)}</strong><span>Không đưa vào index</span></article>
      <article><small>Hàng đợi Visual Search</small><strong>{formatNumber(totals.visual_jobs_pending)}</strong><span>{formatNumber(totals.visual_jobs_processing)} đang xử lý · {formatNumber(totals.visual_jobs_failed)} lỗi</span></article>
    </section>

    <section className="visual-ops-action-card">
      <div><small>ĐIỀU PHỐI BACKFILL</small><h3>{indexAvailable ? actionNeeded ? `${formatNumber(actionNeeded)} ảnh cần được đồng bộ` : "Corpus Visual Search đã đồng bộ" : "Chờ Elasticsearch để xác định coverage"}</h3><p>{indexAvailable ? "Các ảnh current được giữ nguyên; pipeline chỉ cần xử lý ảnh thiếu hoặc stale." : "Các số liệu liên quan Elasticsearch được để ở trạng thái chưa rõ, không mặc định là 0."}</p></div>
      <dl><div><dt>Đang chờ</dt><dd>{formatNumber(totals.visual_jobs_pending)}</dd></div><div><dt>Đang chạy</dt><dd>{formatNumber(totals.visual_jobs_processing)}</dd></div><div><dt>Cần kiểm tra</dt><dd>{formatNumber(totals.visual_jobs_failed)}</dd></div></dl>
    </section>

    <section className="visual-ops-sources">
      <header><div><small>NGUỒN DỮ LIỆU</small><h3>Độ phủ theo từng nguồn</h3></div><time dateTime={coverage.generated_at}>Cập nhật {new Date(coverage.generated_at).toLocaleString("vi-VN")}</time></header>
      {!sources?.sources.length ? <p className="visual-ops-empty">Chưa có nguồn ảnh nào trong tenant hiện tại.</p> : <div className="visual-ops-table-wrap"><table><thead><tr><th>Nguồn</th><th>Phát hiện</th><th>Đã nhập</th><th>Đủ điều kiện</th><th>Current</th><th>Thiếu</th><th>Stale</th><th>Không hỗ trợ</th><th>Sẵn sàng</th></tr></thead><tbody>{sources.sources.map(source => <SourceRow key={source.source_id} source={source} />)}</tbody></table></div>}
    </section>
  </div>;
}
