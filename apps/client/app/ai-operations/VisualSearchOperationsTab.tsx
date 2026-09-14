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

type IconName = "visual" | "discover" | "import" | "eligible" | "indexed" | "sync" | "coverage" | "unsupported" | "queue" | "backfill" | "waiting" | "running" | "review" | "source";

const iconPaths: Record<IconName, string> = {
  visual: "M4 7h3l1.4-2h7.2L17 7h3v11H4V7z M12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  discover: "M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4",
  import: "M12 3v11m0 0l-4-4m4 4l4-4M5 17v3h14v-3",
  eligible: "M12 3l2.2 5.1 5.5.5-4.2 3.6 1.3 5.3-4.8-2.9-4.8 2.9 1.3-5.3-4.2-3.6 5.5-.5L12 3z",
  indexed: "M5 4h11l3 3v13H5V4zm4 9l2 2 4-4",
  sync: "M19 8a7 7 0 0 0-12-2L5 8m0-4v4h4m-4 8a7 7 0 0 0 12 2l2-2m0 4v-4h-4",
  coverage: "M4 19V9m5 10V5m5 14v-8m5 8V3",
  unsupported: "M12 3a9 9 0 1 0 9 9A9 9 0 0 0 12 3zm-4 9h8M12 8v4",
  queue: "M5 6h14M5 12h14M5 18h10",
  backfill: "M4 18h16M6 15V9h4v6m4 0V5h4v10M8 6h.01M16 3h.01",
  waiting: "M12 7v5l3 2M5 3h14v18H5V3z",
  running: "M12 7v5l3 2M3 12a9 9 0 1 0 9-9",
  review: "M12 8v5m0 4h.01M5 4h14v16H5V4z",
  source: "M4 5h16v14H4V5zm3 4h10M7 13h6",
};

function VisualIcon({ name }: { name: IconName }) {
  return <svg className="visual-ops-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

const formatNumber = (value: number | null | undefined) => value == null ? "Chưa rõ" : new Intl.NumberFormat("vi-VN").format(value);
const formatPercent = (value: number | null | undefined) => value == null ? "Chưa rõ" : `${Math.round(value * 100)}%`;

function PipelineStage({ step, label, detail, value, icon, tone = "default" }: {
  step: number; label: string; detail: string; value: number | null | undefined; icon: IconName; tone?: "default" | "success" | "attention";
}) {
  return <article className={`visual-ops-stage visual-ops-stage-${tone}`}>
    <div className="visual-ops-stage-heading"><span className="visual-ops-stage-icon"><VisualIcon name={icon} /></span><small>Bước {step}</small></div>
    <strong>{formatNumber(value)}</strong>
    <b>{label}</b>
    <p>{detail}</p>
  </article>;
}

function MetricCard({ label, detail, value, icon, tone = "default" }: { label: string; detail: string; value: string; icon: IconName; tone?: "default" | "attention" | "success" }) {
  return <article className={`visual-ops-metric visual-ops-metric-${tone}`}>
    <span className="visual-ops-metric-icon"><VisualIcon name={icon} /></span>
    <div><small>{label}</small><strong>{value}</strong><p>{detail}</p></div>
  </article>;
}

function SourceRow({ source }: { source: VisualSearchSourceCoverage }) {
  const searchable = source.ratios.whole_resource_searchable;
  return <tr>
    <td><span className="visual-ops-source-icon"><VisualIcon name="source" /></span><div><b>{source.display_name || "Nguồn chưa đặt tên"}</b><small>{source.source_type}</small></div></td>
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
      <div className="visual-ops-header-copy"><span className="visual-ops-header-icon"><VisualIcon name="visual" /></span><div><small>VISUAL SEARCH</small><h2>Tiến trình lập chỉ mục hình ảnh</h2><p>Theo dõi rõ từng giai đoạn từ ảnh nguồn đến kết quả sẵn sàng tìm kiếm tương tự.</p></div></div>
      <div className={indexAvailable ? "visual-ops-index-state ready" : "visual-ops-index-state unavailable"}><span aria-hidden="true" />{indexAvailable ? "Elasticsearch sẵn sàng" : "Elasticsearch chưa khả dụng"}</div>
    </header>

    <section className="visual-ops-pipeline" aria-label="Visual Search lifecycle">
      <PipelineStage step={1} label="Đã phát hiện" detail="Ảnh hoạt động từ nguồn" value={totals.discovered_images} icon="discover" />
      <PipelineStage step={2} label="Đã nhập" detail="Đã liên kết vào CAM" value={totals.imported_images} icon="import" />
      <PipelineStage step={3} label="Đủ điều kiện" detail="Có thể tạo embedding" value={totals.visual_eligible} icon="eligible" />
      <PipelineStage step={4} label="Đã lập chỉ mục" detail="Projection hiện hành" value={totals.visual_indexed_current} icon="indexed" tone="success" />
      <PipelineStage step={5} label="Cần đồng bộ" detail="Thiếu hoặc stale" value={actionNeeded} icon="sync" tone="attention" />
    </section>

    <section className="visual-ops-summary-grid" aria-label="Visual Search metrics">
      <MetricCard label="Độ phủ import" value={formatPercent(ratios.import_coverage)} detail="Trong ảnh đã phát hiện" icon="coverage" />
      <MetricCard label="Sẵn sàng Visual Search" value={formatPercent(ratios.eligible_visual_coverage)} detail="Trong ảnh đủ điều kiện" icon="indexed" tone="success" />
      <MetricCard label="Không hỗ trợ" value={formatNumber(totals.unsupported_images)} detail="Không đưa vào index" icon="unsupported" />
      <MetricCard label="Hàng đợi Visual Search" value={formatNumber(totals.visual_jobs_pending)} detail={`${formatNumber(totals.visual_jobs_processing)} đang xử lý · ${formatNumber(totals.visual_jobs_failed)} lỗi`} icon="queue" tone="attention" />
    </section>

    <section className="visual-ops-action-card">
      <div className="visual-ops-backfill-copy"><span className="visual-ops-backfill-icon"><VisualIcon name="backfill" /></span><div><small>ĐIỀU PHỐI BACKFILL</small><h3>{indexAvailable ? actionNeeded ? `${formatNumber(actionNeeded)} ảnh cần được đồng bộ` : "Corpus Visual Search đã đồng bộ" : "Chờ Elasticsearch để xác định coverage"}</h3><p>{indexAvailable ? "Các ảnh current được giữ nguyên; pipeline chỉ xử lý ảnh thiếu hoặc stale." : "Các số liệu liên quan Elasticsearch được giữ ở trạng thái chưa rõ, không mặc định là 0."}</p></div></div>
      <dl><div><dt><VisualIcon name="waiting" /> Đang chờ</dt><dd>{formatNumber(totals.visual_jobs_pending)}</dd></div><div><dt><VisualIcon name="running" /> Đang chạy</dt><dd>{formatNumber(totals.visual_jobs_processing)}</dd></div><div><dt><VisualIcon name="review" /> Cần kiểm tra</dt><dd>{formatNumber(totals.visual_jobs_failed)}</dd></div></dl>
    </section>

    <section className="visual-ops-sources">
      <header><div><small>NGUỒN DỮ LIỆU</small><h3>Độ phủ theo từng nguồn</h3><p>Đối chiếu ảnh từ nguồn, khả năng lập chỉ mục và phần còn cần xử lý.</p></div><time dateTime={coverage.generated_at}>Cập nhật {new Date(coverage.generated_at).toLocaleString("vi-VN")}</time></header>
      {!sources?.sources.length ? <p className="visual-ops-empty">Chưa có nguồn ảnh nào trong tenant hiện tại.</p> : <div className="visual-ops-table-wrap"><table><thead><tr><th>Nguồn</th><th>Phát hiện</th><th>Đã nhập</th><th>Đủ điều kiện</th><th>Current</th><th>Thiếu</th><th>Stale</th><th>Không hỗ trợ</th><th>Sẵn sàng</th></tr></thead><tbody>{sources.sources.map(source => <SourceRow key={source.source_id} source={source} />)}</tbody></table></div>}
    </section>
  </div>;
}
