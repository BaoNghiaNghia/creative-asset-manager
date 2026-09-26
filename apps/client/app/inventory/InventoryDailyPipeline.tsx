import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { InventoryApiError, inventoryLifecycleApi, type InventoryHistoricalReplayResult, type InventoryLifecycleHistoryItem, type InventoryLifecycleStage, type InventoryLifecycleStageStatus, type InventoryMorningResetRecoveryPreview, type InventoryStageDetail } from "./api";

const labels: Record<string, string> = { morning_reset: "Reset đầu ngày", afternoon_snapshot: "Đang snapshot", evening_reconcile: "Đang đối soát", verified: "Cần xác minh", completed: "Hoàn tất" };
const symbols: Record<InventoryLifecycleStageStatus, string> = { pending: "○", scheduled: "◌", running: "●", completed: "✓", blocked: "!", review_required: "!", failed: "×", stale: "×" };
type MenuState = { item: InventoryLifecycleHistoryItem; stage: InventoryLifecycleStage; x: number; y: number };

function format(value: string | null) { return value ? new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "—"; }

export async function retryFreshHistoricalReplay(
  run: () => Promise<InventoryHistoricalReplayResult>,
  onRetry?: (nextAttempt: number, error: InventoryApiError) => void,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms)),
): Promise<InventoryHistoricalReplayResult> {
  const retryDelays = [0, 1500, 4000];
  let lastError: unknown;
  for (let attempt = 1; attempt <= retryDelays.length; attempt += 1) {
    if (attempt > 1) await sleep(retryDelays[attempt - 1]);
    try {
      return await run();
    } catch (cause) {
      lastError = cause;
      if (!(cause instanceof InventoryApiError) || !cause.retryable || attempt === retryDelays.length) throw cause;
      onRetry?.(attempt + 1, cause);
    }
  }
  throw lastError;
}

function Stage({ stage, onClick, onMenu }: { stage: InventoryLifecycleStage; onClick: () => void; onMenu: (event: MouseEvent<HTMLButtonElement>) => void }) {
  return <button type="button" className={`inventory-pipeline-stage ${stage.status}`} onClick={onClick} onContextMenu={onMenu} title={`${stage.label}: ${stage.status}`}><span aria-hidden="true">{symbols[stage.status]}</span><small>{stage.label.replace(" đầu ngày", "")}</small></button>;
}
export function isManualRecoveryError(code: string | null | undefined) {
  return ["inventory_morning_reset_missed_safe_window", "inventory_morning_reset_manual_recovery_preview_ready", "stale_evidence"].includes(code || "");
}

const errorHints: Record<string, string> = {
  previous_day_gemini_not_verified: "Reset bị chặn vì Gemini của snapshot ngày trước chưa hoàn tất và chưa được xác minh.",
  inventory_morning_reset_missed_safe_window: "Gemini ngày trước đã được xác minh, nhưng cửa sổ Reset an toàn của hôm nay đã qua. Hệ thống sẽ không tự ghi đè workbook đang được sử dụng; cần recovery thủ công có kiểm soát.",
  inventory_morning_reset_manual_recovery_preview_ready: "Preview recovery đã sẵn sàng. Chỉ các ô Opening/Starting an toàn mới được phép áp dụng; các thao tác clear dữ liệu trong ngày đã bị loại bỏ.",
  stale_evidence: "Workbook đã thay đổi so với preview. Hãy tạo preview mới trước khi áp dụng recovery.",
  inventory_gemini_transport_error: "Không kết nối được Gemini trong lần xử lý này. Hãy kiểm tra credential và trạng thái provider trước khi chạy lại.",
};
const auditSummaryLabels: Record<string, string> = {
  operation_count: "Cell dự kiến thay đổi",
  issue_count: "Vấn đề phát hiện",
  material_count: "Vật tư liên quan",
  warehouse_count: "Kho liên quan",
  tool_rounds: "Vòng tool",
  read_calls: "Lần đọc",
  read_cells: "Cell đã đọc",
  evidence_cell_count: "Cell evidence",
  writes: "Cell đã ghi",
  knowledge_proposal_count: "Kiến thức đề xuất",
  verification_status: "Xác minh",
  plan_hash: "Plan hash",
  staged_summary: "Tóm tắt plan",
};
function auditValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}
function AuditDisclosure({ businessDate, stage }: { businessDate: string; stage: "morning_reset"|"evening_reconcile" }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<InventoryStageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const load = async () => {
    if (detail || loading) return;
    setLoading(true); setError("");
    try { setDetail(await inventoryLifecycleApi.getStageDetail(businessDate, stage)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Chưa có audit chi tiết cho run này."); }
    finally { setLoading(false); }
  };
  const toggle = () => { const next = !open; setOpen(next); if (next) void load(); };
  const assessment = detail?.assessment || {};
  const observations = Array.isArray(assessment.observations) ? assessment.observations as Array<Record<string,unknown>> : [];
  const uncertainties = Array.isArray(assessment.uncertainties) ? assessment.uncertainties as Array<Record<string,unknown>> : [];
  const summaryEntries = detail ? Object.entries(detail.summary).filter(([,value]) => value !== null && value !== undefined && value !== "") : [];
  return <div className="inventory-audit-disclosure">
    <button type="button" className="inventory-audit-toggle" aria-expanded={open} onClick={toggle}>
      <span>{open ? "Ẩn dữ liệu đã xử lý" : "Xem dữ liệu đã xử lý"}</span><b aria-hidden="true">{open ? "−" : "+"}</b>
    </button>
    {open ? <div className="inventory-audit-panel">
      {loading ? <p className="inventory-audit-empty">Đang tải audit…</p> : null}
      {error ? <p className="inventory-audit-empty">{error}</p> : null}
      {detail ? <>
        <div className="inventory-audit-summary">
          {summaryEntries.slice(0, 10).map(([key,value]) => <article key={key}><span>{auditSummaryLabels[key] || key.replaceAll("_"," ")}</span><strong>{auditValue(value)}</strong></article>)}
        </div>
        <div className="inventory-audit-provenance">
          <span>Prompt <b>{detail.prompt.version || detail.prompt.source || "—"}</b></span>
          <span>Knowledge <b>{detail.knowledge.version ?? "—"}</b></span>
          {detail.knowledge.hash ? <code title={detail.knowledge.hash}>{detail.knowledge.hash.slice(0,12)}…</code> : null}
          {detail.model ? <span>Model <b>{detail.model}</b></span> : null}
          <span>Source <b>{detail.source}</b></span>
        </div>
        {detail.read_ranges.length ? <section className="inventory-audit-section"><h4>Gemini đã đọc</h4><div className="inventory-audit-ranges">{detail.read_ranges.map((range,index)=><code key={index}>{auditValue(range)}</code>)}</div></section> : null}
        {assessment.summary || observations.length || uncertainties.length ? <section className="inventory-audit-section">
          <h4>Đánh giá Gemini</h4>
          {assessment.summary ? <p>{auditValue(assessment.summary)}</p> : null}
          {observations.map((observation,index)=><article className="inventory-audit-observation" key={"observation-"+index}><b>{auditValue(observation.code)}</b><p>{auditValue(observation.conclusion)}</p><small>{"Confidence: "+auditValue(observation.confidence)}</small></article>)}
          {uncertainties.map((uncertainty,index)=><article className="inventory-audit-observation warning" key={"uncertainty-"+index}><b>{auditValue(uncertainty.code)}</b><p>{auditValue(uncertainty.message)}</p></article>)}
        </section> : null}
        <section className="inventory-audit-section">
          <h4>{"Thay đổi ("+detail.changes.length+")"}</h4>
          {detail.changes.length ? <div className="inventory-audit-table-wrap"><table className="inventory-audit-table"><thead><tr><th>Sheet / Row</th><th>Cell</th><th>Trước</th><th>Sau</th><th>Nguồn</th><th>Lý do</th><th>Xác minh</th></tr></thead><tbody>
            {detail.changes.map((change)=><tr key={change.sequence}><td><b>{change.sheet}</b><small>{change.row_number ? "Row "+change.row_number : "—"}</small>{change.material_id ? <small>{"Material "+change.material_id}</small> : null}{change.warehouse_id ? <small>{"Kho "+change.warehouse_id}</small> : null}</td><td><code>{change.cell}</code></td><td><code>{auditValue(change.before)}</code></td><td><code>{auditValue(change.after)}</code></td><td>{change.source_cell ? <><b>{change.source_sheet || change.sheet}</b><code>{change.source_cell}</code></> : "—"}</td><td><span>{change.reason || change.operation_type}</span>{change.provenance ? <small>{change.provenance}</small> : null}{change.evidence.length ? <details className="inventory-audit-evidence"><summary>Evidence ({change.evidence.length})</summary><pre>{JSON.stringify(change.evidence,null,2)}</pre></details> : null}</td><td><span className={"inventory-audit-verification "+change.verification_status}>{change.verification_status}</span></td></tr>)}
          </tbody></table></div> : <p className="inventory-audit-empty">Run này không có cell nào cần thay đổi.</p>}
        </section>
        {detail.tool_trace.length ? <section className="inventory-audit-section"><h4>Tool log</h4><ol className="inventory-audit-tools">{detail.tool_trace.map((trace,index)=><li key={index}><b>{auditValue(trace.tool)}</b><code>{JSON.stringify(trace)}</code></li>)}</ol></section> : null}
        {detail.issues?.length ? <section className="inventory-audit-section"><h4>Issues</h4><pre>{JSON.stringify(detail.issues,null,2)}</pre></section> : null}
      </> : null}
    </div> : null}
  </div>;
}
function Details({ item, onClose, onReplay, onReset, onRecoveryPreview, replaying }: { item: InventoryLifecycleHistoryItem; onClose: () => void; onReplay: (item: InventoryLifecycleHistoryItem, mode: "fresh_copy"|"existing_copy", promote: boolean) => void; onReset: (item: InventoryLifecycleHistoryItem) => void; onRecoveryPreview: (item: InventoryLifecycleHistoryItem) => void; replaying: boolean }) {
  return <aside className="inventory-pipeline-details inventory-pipeline-details--wide" role="dialog" aria-label="Chi tiết và nhật ký tiến trình">
    <header><div><span>NHẬT KÝ VẬN HÀNH</span><h3>{item.business_date}</h3><p>Giai đoạn hiện tại: <b>{labels[item.current_stage] || item.current_stage}</b></p></div><button onClick={onClose} aria-label="Đóng">×</button></header>
    {item.invariants?.length ? <section className="inventory-pipeline-log-error">
      <b>Lifecycle invariant ({item.invariants.length})</b>
      {item.invariants.map((invariant) => <div key={invariant.code}><code>{invariant.code}</code><p>{invariant.message}</p></div>)}
    </section> : null}
    <div className="inventory-pipeline-detail-list">{item.stages.map((stage) => {
      const message = stage.error_message || (stage.error_code ? errorHints[stage.error_code] : null);
      return <section key={stage.key} className={"inventory-pipeline-detail-stage " + stage.status}>
        <div className="inventory-pipeline-detail-stage-title"><span aria-hidden="true">{symbols[stage.status]}</span><div><b>{stage.label}</b><small>{stage.status}</small></div></div>
        <ol className="inventory-pipeline-log">
          <li><span>Đã lên lịch</span><time>{stage.scheduled_time || "—"}</time></li>
          <li><span>Bắt đầu</span><time>{format(stage.started_at || null)}</time></li>
          <li><span>Hoàn tất</span><time>{format(stage.completed_at || null)}</time></li>
          {stage.attempt_count !== null && stage.attempt_count !== undefined ? <li><span>Attempts</span><time>{stage.attempt_count}/{stage.max_attempts ?? "—"}</time></li> : null}
          {stage.last_attempt_at ? <li><span>Attempt gần nhất</span><time>{format(stage.last_attempt_at)}</time></li> : null}
          {stage.next_attempt_at && stage.status === "scheduled" ? <li><span>Retry tiếp theo</span><time>{format(stage.next_attempt_at)}</time></li> : null}
          {stage.self_healed ? <li><span>Recovery</span><time>Self-healed từ durable state</time></li> : null}
        </ol>
        {stage.error_code ? <div className="inventory-pipeline-log-error"><b>Chi tiết lỗi</b><code>{stage.error_code}</code>{stage.error_category ? <p>Nhóm: <b>{stage.error_category}</b> · {stage.retryable ? "Có thể retry" : "Không tự retry"}</p> : null}{message ? <p>{message}</p> : null}</div> : null}
        {stage.run_id ? <p className="inventory-pipeline-run-id">Run: <code>{stage.run_id}</code></p> : null}
        {stage.key === "morning_reset" || stage.key === "evening_reconcile" ? <AuditDisclosure businessDate={item.business_date} stage={stage.key} /> : null}
        {stage.key === "morning_reset" && isManualRecoveryError(stage.error_code) ? <div className="inventory-pipeline-detail-actions"><button disabled={replaying} onClick={() => onRecoveryPreview(item)}>Preview recovery an toàn</button></div> : stage.key === "morning_reset" && stage.status === "failed" ? <div className="inventory-pipeline-detail-actions"><button disabled={replaying} onClick={() => onReset(item)}>Chạy lại Reset</button></div> : null}
        {stage.key === "evening_reconcile" && item.files.snapshot_url ? <div className="inventory-pipeline-detail-actions"><button disabled={replaying} onClick={() => onReplay(item, "fresh_copy", true)}>Gemini từ Snapshot sạch</button><button className="secondary" disabled={replaying} onClick={() => onReplay(item, "fresh_copy", false)}>Replay Sandbox</button></div> : null}
      </section>;
    })}</div>
  </aside>;
}

function RecoveryPreview({ item, preview, busy, onClose, onApply, onRefresh }: { item: InventoryLifecycleHistoryItem; preview: InventoryMorningResetRecoveryPreview; busy: boolean; onClose: () => void; onApply: () => void; onRefresh: () => void }) {
  return <aside className="inventory-recovery-preview" role="dialog" aria-modal="true" aria-label="Preview recovery Morning Reset">
    <header><div><span>MANUAL RECOVERY</span><h3>Preview Reset {item.business_date}</h3><p>Chỉ áp dụng các ô Opening/Starting đã được xác minh từ Gemini ngày trước.</p></div><button onClick={onClose} aria-label="Đóng">×</button></header>
    <div className="inventory-recovery-summary">
      <article><span>Ô an toàn</span><strong>{preview.safe_operation_count}</strong></article>
      <article><span>Cần ghi</span><strong>{preview.write_operation_count ?? preview.operations.filter((operation) => operation.needs_write).length}</strong></article>
      <article><span>Đã đúng</span><strong>{preview.operations.filter((operation) => !operation.needs_write).length}</strong></article>
      <article><span>Clear bị loại</span><strong>{preview.excluded_clear_count}</strong></article>
    </div>
    <p className="inventory-recovery-warning">Recovery giữa ngày không xóa movement/input hiện tại. Trước khi Apply, backend sẽ đọc lại source, target và formula; nếu workbook đã thay đổi so với preview thì thao tác sẽ bị chặn.</p>
    <div className="inventory-recovery-table-wrap"><table className="inventory-recovery-table"><thead><tr><th>Đích</th><th>Hiện tại</th><th>Sẽ thành</th><th>Nguồn đã xác minh</th><th>Trạng thái</th></tr></thead><tbody>
      {preview.operations.map((operation) => <tr key={operation.sheet + ":" + operation.cell}><td><b>{operation.sheet}</b><code>{operation.cell}</code></td><td><code>{auditValue(operation.current_value)}</code></td><td><code>{auditValue(operation.desired_value)}</code></td><td><b>{operation.source_sheet}</b><code>{operation.source_cell}</code>{operation.material_id ? <small>{operation.material_id}</small> : null}{operation.warehouse_id ? <small>{operation.warehouse_id}</small> : null}</td><td><span className={operation.needs_write ? "needs-write" : "already-correct"}>{operation.needs_write ? "Sẽ ghi" : "Đã đúng"}</span></td></tr>)}
    </tbody></table></div>
    <footer><button className="secondary" disabled={busy} onClick={onRefresh}>Preview lại</button><button disabled={busy || !preview.plan_hash || preview.safe_operation_count === 0} onClick={onApply}>{busy ? "Đang xử lý…" : "Áp dụng các ô an toàn"}</button></footer>
  </aside>;
}

export function InventoryDailyPipeline({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<{ items: InventoryLifecycleHistoryItem[]; page: number; page_size: number; pages: number } | null>(null);
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(25); const [loading, setLoading] = useState(true); const [error, setError] = useState(false);
  const [selected, setSelected] = useState<InventoryLifecycleHistoryItem | null>(null); const [menu, setMenu] = useState<MenuState | null>(null); const [notice, setNotice] = useState<string | null>(null); const [replaying, setReplaying] = useState(false);
  const [recovery, setRecovery] = useState<{ item: InventoryLifecycleHistoryItem; preview: InventoryMorningResetRecoveryPreview } | null>(null);
  const load = useCallback(async () => { setLoading(true); setError(false); try { setData(await inventoryLifecycleApi.getHistory(page, pageSize)); } catch { setError(true); } finally { setLoading(false); } }, [page, pageSize]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { let timer: number | undefined; const arm = () => { if (timer) window.clearInterval(timer); timer = document.visibilityState === "visible" ? window.setInterval(() => void load(), 30000) : undefined; }; arm(); document.addEventListener("visibilitychange", arm); return () => { if (timer) window.clearInterval(timer); document.removeEventListener("visibilitychange", arm); }; }, [load]);
  useEffect(() => { const close = () => setMenu(null); window.addEventListener("click", close); return () => window.removeEventListener("click", close); }, []);
  const rerunMorningReset = async (item: InventoryLifecycleHistoryItem) => {
    if (!window.confirm(`Chạy lại Reset cho ${item.business_date}? Chỉ khả dụng trong cửa sổ ±1 giờ quanh giờ Reset.`)) return;
    setReplaying(true); setNotice(null);
    try {
      const result = await inventoryLifecycleApi.rerunMorningReset(item.business_date);
      setNotice(`Reset đầu ngày: ${result.status}.`);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Không thể chạy lại Reset.");
    } finally { setReplaying(false); }
  };
  const previewMorningResetRecovery = async (item: InventoryLifecycleHistoryItem) => {
    setReplaying(true); setNotice(`Đang tạo preview recovery cho ${item.business_date}…`);
    try {
      const preview = await inventoryLifecycleApi.previewMorningResetRecovery(item.business_date);
      setRecovery({ item, preview });
      setNotice(`Preview recovery: ${preview.safe_operation_count} ô an toàn, ${preview.excluded_clear_count} thao tác clear đã bị loại.`);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Không thể tạo preview recovery.");
    } finally { setReplaying(false); }
  };
  const applyMorningResetRecovery = async () => {
    if (!recovery?.preview.plan_hash) return;
    const writes = recovery.preview.write_operation_count ?? recovery.preview.operations.filter((operation) => operation.needs_write).length;
    if (!window.confirm(`Áp dụng recovery an toàn cho ${recovery.item.business_date}?\n\n${writes} ô sẽ được ghi. ${recovery.preview.excluded_clear_count} thao tác clear sẽ KHÔNG được thực hiện. Backend sẽ kiểm tra drift lại trước khi ghi.`)) return;
    setReplaying(true); setNotice(`Đang áp dụng recovery cho ${recovery.item.business_date}…`);
    try {
      const result = await inventoryLifecycleApi.applyMorningResetRecovery(recovery.item.business_date, recovery.preview.plan_hash);
      setNotice(`Recovery hoàn tất: ${result.applied_count} ô đã ghi, ${result.already_correct_count} ô đã đúng; ${result.excluded_clear_count} clear được giữ nguyên.`);
      setRecovery(null);
      setSelected(null);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Không thể áp dụng recovery.");
      if (cause instanceof InventoryApiError && cause.code === "stale_evidence") setRecovery(null);
      await load();
    } finally { setReplaying(false); }
  };
  const replayGemini = async (
    item: InventoryLifecycleHistoryItem,
    mode: "fresh_copy" | "existing_copy",
    promote: boolean,
  ) => {
    const description = !promote
      ? "Replay sandbox từ Snapshot sạch. Kết quả sẽ không thay thế Gemini chính thức."
      : mode === "fresh_copy"
        ? "Tạo một Gemini copy mới từ Snapshot bất biến và chỉ promote khi xác minh thành công."
        : "Chạy lại trên Gemini file hiện có.";
    if (!window.confirm(`${description}\n\nNgày: ${item.business_date}\nTiếp tục?`)) return;
    setReplaying(true); setNotice(`Đang chạy lại Gemini cho ${item.business_date}…`);
    try {
      const result = mode === "fresh_copy"
        ? await retryFreshHistoricalReplay(
            () => inventoryLifecycleApi.replayHistoricalGemini(item.business_date, mode, promote),
            (nextAttempt, error) => setNotice(`Replay ${item.business_date} gặp lỗi ${error.category || error.code || "tạm thời"}; đang thử fresh copy lần ${nextAttempt}/3…`),
          )
        : await inventoryLifecycleApi.replayHistoricalGemini(item.business_date, mode, promote);
      const recovery = result.morning_reset_recovery;
      setNotice(
        result.promoted
          ? `Gemini ${item.business_date} đã xác minh và promote thành công.${recovery?.status === "completed" ? " Morning Reset hôm nay cũng đã được tự động phục hồi." : recovery && !["historical_day_skipped", "not_applicable", "not_scheduled"].includes(recovery.status) ? ` Morning Reset recovery: ${recovery.status}.` : ""}`
          : `Replay ${item.business_date} hoàn tất: ${result.status} / ${result.verification_status}. Không thay đổi file chính thức.`
      );
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Không thể chạy lại Gemini.");
    } finally { setReplaying(false); }
  };
  const batchCandidates = (data?.items || []).filter((item) => {
    if (!item.files.snapshot_url) return false;
    const evening = item.stages.find((stage) => stage.key === "evening_reconcile");
    const verified = item.stages.find((stage) => stage.key === "verified");
    return (evening?.status === "failed" || evening?.status === "review_required") && verified?.status !== "completed";
  });
  const replayFailedDays = async () => {
    if (!batchCandidates.length || replaying) return;
    if (!window.confirm(
      `Chạy lại tuần tự ${batchCandidates.length} ngày lỗi trên trang hiện tại?\n\nMỗi ngày sẽ tạo Gemini copy mới từ Snapshot và chỉ promote khi verify thành công.`
    )) return;
    setReplaying(true);
    let promoted = 0; let notPromoted = 0; let failed = 0; let stoppedReason: string | null = null;
    try {
      for (let index = 0; index < batchCandidates.length; index += 1) {
        const item = batchCandidates[index];
        setNotice(`Đang replay ${index + 1}/${batchCandidates.length}: ${item.business_date}…`);
        try {
          const result = await retryFreshHistoricalReplay(
            () => inventoryLifecycleApi.replayHistoricalGemini(item.business_date, "fresh_copy", true),
            (nextAttempt, error) => setNotice(`Replay ${item.business_date} gặp lỗi ${error.category || error.code || "tạm thời"}; đang thử fresh copy lần ${nextAttempt}/3…`),
          );
          if (result.promoted) promoted += 1; else notPromoted += 1;
        } catch (cause) {
          failed += 1;
          if (cause instanceof InventoryApiError) {
            const reason = cause.category || cause.code || cause.message;
            stoppedReason = `Batch dừng tại ${item.business_date}: ${reason}. Đã promote ${promoted}, chưa promote ${notPromoted}, lỗi ${failed}.`;
            break;
          }
        }
      }
      setNotice(stoppedReason || `Batch replay hoàn tất: ${promoted} promote, ${notPromoted} chưa promote, ${failed} lỗi.`);
      await load();
    } finally { setReplaying(false); }
  };
  if (loading && !data) return <section className="inventory-empty">Đang tải tiến trình Inventory…</section>;
  if (error && !data) return <section className="inventory-empty"><p>Không thể tải tiến trình.</p><button onClick={() => void load()}>Thử lại</button></section>;
  return <section className={`inventory-pipeline${embedded ? " inventory-pipeline--embedded" : ""}`}><header><div><span>INVENTORY</span><h2>Tiến trình kiểm kho</h2><p>Theo dõi mỗi ngày làm việc từ reset đến xác minh Gemini.</p></div><div className="inventory-pipeline-header-actions">{batchCandidates.length ? <button onClick={() => void replayFailedDays()} disabled={replaying}>{replaying ? "Đang replay…" : `Chạy lại ${batchCandidates.length} ngày lỗi`}</button> : null}<button onClick={() => void load()} disabled={loading || replaying}>{loading ? "Đang làm mới…" : "Làm mới"}</button></div></header>{error ? <p className="inventory-error">Không thể tải dữ liệu mới nhất.</p> : null}{notice ? <p className="inventory-error">{notice}</p> : null}{!data?.items.length ? <div className="inventory-pipeline-empty"><b>Chưa có tiến trình kiểm kho.</b><span>Tiến trình của ngày làm việc sẽ xuất hiện ở đây khi automation bắt đầu.</span></div> : <div className="inventory-pipeline-scroll"><table><thead><tr><th>NGÀY KIỂM</th><th>FILE</th><th>GIAI ĐOẠN HIỆN TẠI</th><th>LUỒNG XỬ LÝ</th><th>CẬP NHẬT</th><th>CẦN XỬ LÝ</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.business_date} onClick={() => setSelected(item)}><td><b>{new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium" }).format(new Date(`${item.business_date}T00:00:00`))}</b></td><td className="inventory-pipeline-files">{item.files.shared_url ? <a href={item.files.shared_url} target="_blank" rel="noreferrer">Shared ↗</a> : null}{item.files.snapshot_url ? <a href={item.files.snapshot_url} target="_blank" rel="noreferrer">Snapshot ↗</a> : <span>Snapshot: Chưa tạo</span>}{item.files.gemini_url ? <a href={item.files.gemini_url} target="_blank" rel="noreferrer">Gemini ↗</a> : null}</td><td><span className={`inventory-pipeline-chip ${item.overall_status}`}>{labels[item.current_stage] || item.current_stage}</span></td><td><div className="inventory-pipeline-flow">{item.stages.map((stage, index) => <div key={stage.key} className="inventory-pipeline-node"><Stage stage={stage} onClick={() => setSelected(item)} onMenu={(event) => { event.preventDefault(); event.stopPropagation(); setMenu({ item, stage, x: event.clientX, y: event.clientY }); }} />{index < item.stages.length - 1 ? <i className={stage.status === "completed" ? "done" : ""} /> : null}</div>)}</div></td><td>{format(item.updated_at)}</td><td>{item.action_required ? <button className="inventory-pipeline-action" onClick={(event) => { event.stopPropagation(); setSelected(item); }}>{item.action_required.label}</button> : "—"}</td></tr>)}</tbody></table></div>}<footer><label>Số mục mỗi trang <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}>{[25, 50, 100].map((size) => <option key={size}>{size}</option>)}</select></label><div><button disabled={!data || page === 1} onClick={() => setPage(page - 1)}>Trước</button><b>{data?.page || 1} / {data?.pages || 1}</b><button disabled={!data || page >= (data?.pages || 1)} onClick={() => setPage(page + 1)}>Tiếp</button></div></footer>{menu ? <div role="menu" className="inventory-pipeline-context-menu" style={{ left: menu.x, top: menu.y }} onClick={(event) => event.stopPropagation()}><button role="menuitem" onClick={() => { setSelected(menu.item); setMenu(null); }}>Chi tiết</button>{menu.stage.key === "morning_reset" ? (isManualRecoveryError(menu.stage.error_code) ? <button role="menuitem" disabled={replaying} onClick={() => { const item = menu.item; setMenu(null); void previewMorningResetRecovery(item); }}>Preview recovery an toàn</button> : <button role="menuitem" disabled={replaying} onClick={() => { const item = menu.item; setMenu(null); void rerunMorningReset(item); }}>Chạy lại Reset</button>) : null}{menu.stage.key === "evening_reconcile" ? <><button role="menuitem" disabled={replaying || !menu.item.files.snapshot_url} onClick={() => { const item = menu.item; setMenu(null); void replayGemini(item, "fresh_copy", true); }}>Gemini từ Snapshot sạch</button><button role="menuitem" disabled={replaying || !menu.item.files.snapshot_url} onClick={() => { const item = menu.item; setMenu(null); void replayGemini(item, "fresh_copy", false); }}>Replay Sandbox</button></> : null}</div> : null}{selected ? <Details item={selected} onClose={() => setSelected(null)} onReplay={(item, mode, promote) => void replayGemini(item, mode, promote)} onReset={(item) => void rerunMorningReset(item)} onRecoveryPreview={(item) => void previewMorningResetRecovery(item)} replaying={replaying} /> : null}{recovery ? <RecoveryPreview item={recovery.item} preview={recovery.preview} busy={replaying} onClose={() => setRecovery(null)} onRefresh={() => void previewMorningResetRecovery(recovery.item)} onApply={() => void applyMorningResetRecovery()} /> : null}</section>;
}