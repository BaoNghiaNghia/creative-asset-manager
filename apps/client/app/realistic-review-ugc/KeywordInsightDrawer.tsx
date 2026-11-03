import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { generateKeywordCreativeBrief, getKeywordIntelligence } from "./api";
import type { KeywordIntelligence, KeywordVolume } from "./types";
import "./KeywordInsightDrawer.css";

type Tab = "overview" | "buyer" | "creative" | "learning" | "evidence";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "buyer", label: "Buyer" },
  { key: "creative", label: "Creative" },
  { key: "learning", label: "Learning" },
  { key: "evidence", label: "Evidence" },
];
const RECOMMENDATIONS: Record<KeywordIntelligence["score"]["recommendation"], string> = {
  prioritize: "Ưu tiên thử nghiệm",
  explore: "Explore",
  hold: "Cần xem lại",
  needs_analysis: "Thiếu dữ liệu",
};

function InsightEmpty({ title, detail }: { title: string; detail: string }) {
  return <div className="rrugc-insight-empty"><strong>{title}</strong><p>{detail}</p></div>;
}

function DimensionRow({ dimension }: { dimension: KeywordIntelligence["score"]["dimensions"][number] }) {
  return <article className="rrugc-insight-dimension">
    <div className="rrugc-insight-dimension-header">
      <strong>{dimension.label}</strong>
      <span>{dimension.score == null ? "Chưa đánh giá" : dimension.score + "/100"} <small>· {dimension.weight}%</small></span>
    </div>
    <div className="rrugc-insight-progress" aria-hidden="true">
      {dimension.score != null && <span style={{ width: dimension.score + "%" }} />}
    </div>
    <p>{dimension.note}</p>
  </article>;
}

function TextField({ label, value }: { label: string; value: string | null }) {
  return <div className="rrugc-insight-text-field">
    <small>{label}</small>
    <p>{value || "Chưa phân tích"}</p>
  </div>;
}

export function KeywordInsightDrawer({
  item, onClose, onPickChange, onOpenMetrics,
}: {
  item: KeywordVolume | null;
  onClose: () => void;
  onPickChange?: (keywordId: string, picked: boolean) => void;
  onOpenMetrics?: (item: KeywordVolume) => void;
}) {
  const titleId = useId();
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [tab, setTab] = useState<Tab>("overview");
  const [report, setReport] = useState<KeywordIntelligence | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueMessage, setQueueMessage] = useState("");

  useEffect(() => {
    if (!item) return;
    setTab("overview");
    setReport(null);
    setError("");
    setQueueMessage("");
  }, [item?.id]);

  useEffect(() => {
    if (!item) return;
    const controller = new AbortController();
    setLoading(true);
    getKeywordIntelligence(item.id, controller.signal)
      .then(result => {
        if (!controller.signal.aborted) {
          setReport(result);
          setError("");
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted) setError(
          reason instanceof Error ? reason.message : "Không tải được Keyword Intelligence.",
        );
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [item?.id, refresh]);

  const status = report && report.keyword_id === item?.id ? report.brief_status : null;
  useEffect(() => {
    if (!item || (status !== "queued" && status !== "running")) return;
    const interval = window.setInterval(() => {
      if (!document.hidden) setRefresh(value => value + 1);
    }, 10000);
    return () => window.clearInterval(interval);
  }, [item?.id, status]);

  useEffect(() => {
    if (!item) return;
    const previousOverflow = document.body.style.overflow;
    const activeElement = document.activeElement;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const keyHandler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
        "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), summary",
      ) || []).filter(node => node.getClientRects().length > 0 || node.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", keyHandler);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", keyHandler);
      if (activeElement instanceof HTMLElement && activeElement.isConnected) activeElement.focus();
    };
  }, [item?.id]);

  if (!item) return null;
  const current = report?.keyword_id === item.id ? report : null;
  const score = current?.score;
  const ready = current?.brief_status === "ready";
  const analyzing = current?.brief_status === "queued" || current?.brief_status === "running";

  async function startBrief() {
    if (!item || !item.picked || queueBusy || analyzing) return;
    setQueueBusy(true);
    setError("");
    setQueueMessage("");
    try {
      await generateKeywordCreativeBrief(item.id);
      setQueueMessage("Đã gửi yêu cầu phân tích. CAM sẽ sử dụng hàng đợi Creative Brief hiện có.");
      setRefresh(value => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể xếp hàng Creative Brief.");
    } finally {
      setQueueBusy(false);
    }
  }

  const body = <div className="rrugc-insight-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section ref={drawerRef} className="rrugc-insight-drawer" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <header className="rrugc-insight-header">
        <div className="rrugc-insight-title">
          <small>STAGE 0 · KEYWORD INTELLIGENCE V2</small>
          <h2 id={titleId}>{item.keyword}</h2>
          <p>Buyer psychology · Creative DNA · Embroidery fit · Feedback</p>
        </div>
        <button ref={closeRef} type="button" className="rrugc-insight-close" aria-label="Close Keyword Insight" onClick={onClose}>×</button>
      </header>
      <nav className="rrugc-insight-tabs" aria-label="Keyword intelligence sections">
        {TABS.map(entry => <button key={entry.key} type="button"
          className={tab === entry.key ? "is-active" : ""}
          aria-current={tab === entry.key ? "page" : undefined}
          onClick={() => setTab(entry.key)}>{entry.label}</button>)}
      </nav>
      <div className="rrugc-insight-scroll">
        {loading && !current && <div className="rrugc-insight-loading" role="status">
          <span className="rrugc-insight-skeleton" /><span className="rrugc-insight-skeleton short" />
          <span>Đang tải thông tin đã lưu…</span>
        </div>}
        {error && <p className="rrugc-insight-error" role="alert">{error}</p>}
        {queueMessage && <p className="rrugc-insight-notice" role="status">{queueMessage}</p>}
        {current && <>
          {tab === "overview" && <div className="rrugc-insight-stack">
            <div className="rrugc-insight-score-card">
              <div>
                <span className="rrugc-insight-kicker">OPPORTUNITY · PRELIMINARY</span>
                <div className="rrugc-insight-score-value">
                  <strong>{score?.value ?? "—"}</strong><small>{score?.value == null ? "Cần thêm dữ liệu" : "/100"}</small>
                </div>
                <span className={"rrugc-insight-recommendation is-" + score?.recommendation}>
                  {RECOMMENDATIONS[score?.recommendation ?? "needs_analysis"]}
                </span>
              </div>
              <div className="rrugc-insight-score-meta">
                <b>{score?.coverage}%</b><span>Coverage</span>
                <b>{score?.confidence === "medium" ? "Trung bình" : "Thấp"}</b><span>Confidence</span>
              </div>
            </div>
            <p className="rrugc-insight-explanation">{score?.explanation}</p>
            <div className="rrugc-insight-section-heading">
              <strong>Opportunity Breakdown</strong>
              <small>5 tiêu chí · chỉ chấm khi có dữ liệu</small>
            </div>
            {score?.dimensions.map(dimension => <DimensionRow dimension={dimension} key={dimension.key} />)}
            <div className="rrugc-insight-note">Không phải dự báo doanh số hoặc chứng nhận khả năng thêu. Originality chưa được chấm khi chưa có Visual Similarity QA.</div>
          </div>}
          {tab === "buyer" && <div className="rrugc-insight-stack">
            <div className="rrugc-insight-section-heading"><strong>Buyer Psychology</strong><small>Redesign Nurse + Pet</small></div>
            {ready ? <>
              <div className="rrugc-insight-note">Giả thuyết từ Creative Brief V3 — không phải kết quả phỏng vấn khách hàng hoặc bằng chứng bán hàng.</div>
              <TextField label="Người đội / nhóm khách hàng" value={current.buyer.audience}/>
              <TextField label="Động lực mua / tặng quà" value={current.buyer.purchase_motivation}/>
              <TextField label="Ý nghĩa & cảm xúc" value={current.buyer.emotion}/>
              <TextField label="Chiến lược phù hợp" value={current.buyer.strategy}/>
            </> : <InsightEmpty title="Chưa có Buyer Analysis" detail="Pick keyword để chạy Creative Brief. CAM không tự suy đoán chân dung người mua từ search volume."/>}
          </div>}
          {tab === "creative" && <div className="rrugc-insight-stack">
            <div className="rrugc-insight-section-heading"><strong>Creative Potential</strong><small>Typography + Meaning-first</small></div>
            <div className="rrugc-insight-metrics-row">
              <div><strong>{current.creative.direction_count || "—"}</strong><small>Ý tưởng bố cục</small></div>
              <div><strong>1 / 4 / 4</strong><small>Stage 1 target</small></div>
            </div>
            {current.creative.hook && <TextField label="Creative Hook" value={current.creative.hook}/>}
            {current.creative.directions.length ? current.creative.directions.map(direction =>
              <article className="rrugc-insight-concept" key={direction.mode}>
                <div><span>{direction.label}</span><small>{direction.variants.length} variants</small></div>
                <strong>{direction.name}</strong>
                <p>{direction.familiar_format || "Không ghi nhận format"}</p>
                {direction.creative_twist && <p><b>One Rule Break:</b> {direction.creative_twist}</p>}
                {direction.creative_hook && <p><b>Hook:</b> {direction.creative_hook}</p>}
                <details><summary>Xem {direction.variants.length} hướng bố cục</summary>
                  <ol>{direction.variants.map((variant, index) => <li key={index}>{variant}</li>)}</ol>
                </details>
              </article>,
            ) : <InsightEmpty title="Chưa có Creative DNA" detail="Creative Brief sẽ đề xuất Typography-first, Creative Twist và Explore. Không tạo thêm ảnh khi chỉ mở bảng phân tích."/>}
            <div className="rrugc-insight-section-heading"><strong>Embroidery Preflight</strong><small>Chỉ đánh giá sơ bộ</small></div>
            <div className="rrugc-insight-metrics-row">
              <div><strong>{current.embroidery.character_count}</strong><small>Ký tự</small></div>
              <div><strong>{current.embroidery.word_count}</strong><small>Từ</small></div>
              <div><strong>{current.embroidery.preflight_score}</strong><small>Text fit /100</small></div>
            </div>
            {current.embroidery.warnings.map((warning, index) => <p className="rrugc-insight-note" key={index}>{warning}</p>)}
          </div>}
          {tab === "learning" && <div className="rrugc-insight-stack">
            <div className="rrugc-insight-section-heading"><strong>Pick / Reject Learning</strong><small>Exact keyword · tenant-scoped</small></div>
            <div className="rrugc-insight-metrics-row">
              <div><strong>{current.learning.picked_votes}</strong><small>Picked</small></div>
              <div><strong>{current.learning.rejected_votes}</strong><small>Rejected</small></div>
              <div><strong>{current.learning.total_votes}</strong><small>Feedback</small></div>
            </div>
            {current.learning.total_votes === 0
              ? <InsightEmpty title="Chưa có feedback" detail="Khi Pick hoặc Reject design tại Stage 1, CAM sẽ dùng phản hồi cho lần tạo tiếp theo. Không đánh đồng Reject ảnh với Reject keyword."/>
              : <>
                {current.learning.rejected.map((entry, index) => <article className="rrugc-insight-learning-row" key={index}>
                  <strong>{entry.mode} · {entry.variant} <small>×{entry.count}</small></strong>
                  <span>{entry.reason.replace(/_/g, " ")}</span><p>{entry.guidance}</p>
                </article>)}
                {current.learning.general_rejections.map((entry, index) => <article className="rrugc-insight-learning-row" key={"g"+index}>
                  <strong>Legacy design <small>×{entry.count}</small></strong>
                  <span>{entry.reason.replace(/_/g, " ")}</span><p>{entry.guidance}</p>
                </article>)}
              </>}
            <div className="rrugc-insight-section-heading"><strong>Cross-keyword feedback</strong>
              <small>{current.learning.cross_keyword?.cohort || "Chưa phân nhóm"}</small></div>
            {current.learning.cross_keyword?.eligible ? <>
              <div className="rrugc-insight-metrics-row">
                <div><strong>{current.learning.cross_keyword.distinct_keywords}</strong><small>Keyword tương đồng</small></div>
                <div><strong>{current.learning.cross_keyword.vote_count}</strong><small>Lượt đánh giá</small></div>
                <div><strong>{current.learning.cross_keyword.patterns.length}</strong><small>Bài học đủ ngưỡng</small></div>
              </div>
              {current.learning.cross_keyword.patterns.map(pattern => <article key={pattern.reason} className="rrugc-insight-learning-row">
                <strong>{pattern.reason.replace(/_/g, " ")}</strong>
                <span>{pattern.distinct_keywords} keyword · {pattern.rejected_outputs} ảnh Reject</span>
                <p>{pattern.guidance}</p>
              </article>)}
              {!current.learning.cross_keyword.patterns.length && <p className="rrugc-insight-note">Đã có phản hồi liên-keyword nhưng chưa có một lỗi lặp lại đủ mạnh để điều chỉnh prompt.</p>}
            </> : <div className="rrugc-insight-note">Chưa đủ mẫu để học liên-keyword. Yêu cầu tối thiểu 3 keyword khác nhau và 6 lượt đánh giá trong cùng nhóm chủ đề. Không tự cấm cả phong cách từ một ảnh Reject.</div>}
            <div className="rrugc-insight-note">Học liên-keyword chỉ là gợi ý điều chỉnh bố cục. Reject một ảnh không đồng nghĩa Reject keyword hoặc dự đoán doanh số.</div>
          </div>}
          {tab === "evidence" && <div className="rrugc-insight-stack">
            <div className="rrugc-insight-section-heading"><strong>Market & Visual Evidence</strong><small>Dữ liệu đã lưu, không tìm kiếm mới</small></div>
            <div className="rrugc-insight-metrics-row">
              <div><strong>{current.evidence.volume == null ? "—" : current.evidence.volume.toLocaleString()}</strong><small>Searches / mo</small></div>
              <div><strong>{current.evidence.competition || "—"}</strong><small>Ads competition</small></div>
            </div>
            <TextField label="Nguồn volume" value={current.evidence.provider === "pending" ? null : current.evidence.provider}/>
            <TextField label="Trademark screening" value={current.evidence.trademark_status}/>
            <div className="rrugc-insight-note">Kết quả trademark từ nguồn sàng lọc không phải xác nhận pháp lý. Cần kiểm tra thủ công trước khi thương mại hóa.</div>
            <div className="rrugc-insight-evidence-grid">
              <span>Source Pin <b>{current.evidence.source_pin_available ? "Đã lưu" : "Chưa có"}</b></span>
              <span>Source Image <b>{current.evidence.source_image_available ? "Đã lưu" : "Chưa có"}</b></span>
              <span>G3 Typography DNA <b>{current.visual?.analyzed_count ? current.visual.analyzed_count + " approved reference(s)" : "Chưa có"}</b></span>
              <span>Output đã Reject có hash <b>{current.visual?.rejected_stage1_fingerprint_count ?? 0}</b></span>
            </div>
            <div className="rrugc-insight-metrics-row">
              <div><strong>{current.evidence.three_month_change_pct == null ? "—" : current.evidence.three_month_change_pct.toFixed(1) + "%"}</strong><small>3-mo trend</small></div>
              <div><strong>{current.evidence.yoy_change_pct == null ? "—" : current.evidence.yoy_change_pct.toFixed(1) + "%"}</strong><small>YoY trend</small></div>
              <div><strong>{current.evidence.fetched_at ? new Date(current.evidence.fetched_at).toLocaleDateString() : "—"}</strong><small>Market checked</small></div>
            </div>
            {current.visual?.items?.length ? <>
              <div className="rrugc-insight-section-heading"><strong>G3 Typography DNA · approved references</strong>
                <small>{current.visual.analyzed_count} kết quả</small></div>
              {current.visual.items.slice(0, 8).map(dna => <article key={dna.reference_id} className="rrugc-insight-concept">
                <div><span>{dna.style_family}</span><small>Confidence {Math.round(dna.confidence * 100)}%</small></div>
                <strong>{dna.font_mood || "Typography profile"}</strong>
                <p>{dna.layout_nodes} text nodes · {dna.layout_edges} layout relations · fingerprint {dna.perceptual_hash_available ? "available" : "unavailable"}</p>
              </article>)}
              <div className="rrugc-insight-note">{current.visual.similar_pairs.length} cặp reference có bố cục gần giống (dHash distance ≤ 6). Đây là so sánh vùng typography của reference, không phải xác minh design Stage 1.</div>
              {current.visual.similar_pairs.slice(0, 5).map(pair => <div key={pair.left_reference_id + pair.right_reference_id} className="rrugc-insight-learning-row">
                <strong>Similar typography ROI · distance {pair.distance}</strong>
                <span>{pair.left_reference_id.slice(0, 8)} · {pair.right_reference_id.slice(0, 8)}</span>
              </div>)}
            </> : <p className="rrugc-insight-note">Chưa có reference G3 được duyệt, được phép sử dụng và phân tích thành công gắn với keyword này. CAM không tự quét ảnh Scout khi mở panel.</p>}
            <div className="rrugc-insight-note">Không đồng nhất Google Ads volume với doanh số Etsy. G3 chỉ dùng dữ liệu tham khảo có xác nhận quyền sử dụng; perceptual hash chỉ là tín hiệu giống hình, không phải chứng nhận bản quyền hay độ độc đáo.</div>
          </div>}
        </>}
      </div>
      <footer className="rrugc-insight-footer">
        <div className="rrugc-insight-footer-status">
          <strong>{ready ? "Creative Brief: Ready" : analyzing ? "Creative Brief: " + current?.brief_status : "Creative Brief: " + (current?.brief_status || "—")}</strong>
          <small>{ready ? "Đã dùng kết quả phân tích hiện có" : "Không chạy AI tự động khi mở Insight"}</small>
        </div>
        <div className="rrugc-insight-footer-actions">
          <button type="button" className="rrugc-insight-btn secondary"
            onClick={() => { onClose(); onOpenMetrics?.(item); }}>Metrics</button>
          <button type="button" className="rrugc-insight-btn secondary"
            disabled={loading} onClick={() => setRefresh(value => value + 1)}>Refresh</button>
          {item.picked ? <button type="button" className="rrugc-insight-btn primary"
            disabled={queueBusy || analyzing || ready} onClick={() => void startBrief()}>
            {ready ? "Đã phân tích" : analyzing ? "Đang xử lý" : queueBusy ? "Đang gửi…" : "Analyze"}
          </button> : <button type="button" className="rrugc-insight-btn primary"
            onClick={() => onPickChange?.(item.id, true)} disabled={!onPickChange}>Pick & Analyze</button>}
        </div>
      </footer>
    </section>
  </div>;

  return createPortal(body, document.body);
}
