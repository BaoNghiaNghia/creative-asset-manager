import { useEffect, useState } from "react";

import {
  InventoryApiError,
  inventoryApi,
  inventoryDailySheetApi,
  type InventoryDailyRun,
  type InventoryDailySheetStatus,
} from "../inventory/api";
import { InventoryApp, type InventoryPage } from "../inventory/InventoryApp";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";

type InventoryDailyState = {
  status: InventoryDailySheetStatus;
  run: InventoryDailyRun | null;
};

const businessDate = (value: string | null | undefined) => {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  return year && month && day ? day + "/" + month + "/" + year : value;
};

type QuickActionIconName = "sheet" | "refresh";

function QuickActionIcon({ name }: { name: QuickActionIconName }) {
  const paths = {
    sheet: <><path d="M6 3.5h8l4 4v13H6z" /><path d="M14 3.5v4h4M8.5 11h7M8.5 14h7M8.5 17h4" /></>,
    refresh: <><path d="M19 8.5V4.5m0 0h-4m4 0-3 3A7 7 0 1 0 19 12" /></>,
  };
  return <svg data-ui-icon className="ops-inventory-action-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

const dateTime = (value: string | null | undefined) => value
  ? new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value))
  : "—";

const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("vi-VN") : "—";

const operationalLabel = (value: InventoryDailySheetStatus["operational_state"]) => ({
  healthy: "Hạ tầng sẵn sàng",
  degraded: "Cần kiểm tra",
  disabled: "Đang tắt",
}[value]);

const processStatusLabel = (value: string | null | undefined) => {
  if (!value) return "Chưa chạy";
  return ({
    completed: "Hoàn tất",
    settled: "Đã hoàn tất",
    running: "Đang chạy",
    ready: "Sẵn sàng",
    writing: "Đang ghi dữ liệu",
    retryable_failure: "Sẽ thử lại",
    permanent_failure: "Không thể tiếp tục",
    terminal_failure: "Không thể tiếp tục",
    failed: "Thất bại",
    blocked: "Bị chặn",
    review_required: "Cần xem lại",
    stale: "Dữ liệu cũ",
    pending: "Chờ chạy",
    scheduled: "Đã lên lịch",
  } as Record<string, string>)[value] || value;
};

export function InventoryDailyTab() {
  const [value, setValue] = useState<InventoryDailyState | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    setError("");
    inventoryDailySheetApi.getStatus().then(async status => {
      let run: InventoryDailyRun | null = null;
      if (status.execution_mode !== "v4_slots") {
        try {
          run = await inventoryApi.getDailyRun(status.working_business_date);
        } catch (failure) {
          if (!(failure instanceof InventoryApiError) || failure.status !== 404) throw failure;
        }
      }
      if (alive) setValue({ status, run });
    }).catch(failure => {
      if (alive) setError(failure instanceof Error ? failure.message : "Không thể tải dữ liệu Inventory hôm nay.");
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
  }, [reload]);

  // Status-only refresh. The Inventory scheduler, not this browser, owns every job.
  useEffect(() => {
    const timer = window.setInterval(() => setReload(value => value + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  if (!value) return <div className="ops-content ops-inventory-daily">
    <WorkspacePageHeader route="inventory" title="Tổng quan kiểm kê tự động" eyebrow="Inventory · Operations"
      className="cam-header-v18 cam-inventory-header-v18" description="Theo dõi chu kỳ kiểm kê tự động tại Asia/Ho_Chi_Minh." actions={<WorkspaceBackToAssets />} />
    <div className={"ops-inventory-state" + (error ? " ops-inventory-error" : "")} role={error ? "alert" : undefined} aria-busy={loading}>
      {loading ? "Đang tải dữ liệu Inventory hằng ngày…" : <><strong>Không thể tải Inventory</strong><p>{error}</p><button type="button" onClick={() => setReload(current => current + 1)}>Thử lại</button></>}
    </div>
  </div>;
  // Keep the overview and any open human-review dialog mounted on refresh failures.
  // A transient network outage must never discard the last verified status.
  return <InventoryDailyOverview {...value} refreshError={error} onRefresh={() => setReload(current => current + 1)} />;
}

export function InventoryDailyOverview({ status, run, refreshError = "", onRefresh = () => undefined }: InventoryDailyState & { onRefresh?: () => void; refreshError?: string }) {
  const [modalPage, setModalPage] = useState<InventoryPage | null>(null);
  const reconciliation = status.last_reconciliation;
  const snapshot = status.last_snapshot;
  const summary = reconciliation?.summary || {};
  const blockers = run?.blockers || [];
  const healthy = status.operational_state === "healthy";
  const usesV4Slots = status.execution_mode === "v4_slots";
  const lifecycle: NonNullable<InventoryDailySheetStatus["lifecycle"]> = status.lifecycle || {
    business_date: status.working_business_date,
    morning_reset: { status: status.carry_forward?.status || "pending", scheduled_time: status.carry_forward_time, source_business_date: "", source_gemini_file_id: null },
    afternoon_snapshot: { status: snapshot?.status || "pending", scheduled_time: status.snapshot_time, snapshot_file_id: snapshot?.snapshot_file_id || null, gemini_file_id: snapshot?.gemini_file_id || null },
    evening_reconcile: { status: reconciliation?.status || "pending", scheduled_time: status.reconcile_time, verified: reconciliation?.status === "completed", run_id: null, plan_hash: null, prompt_version: null, prompt_hash: null },
  };
  const lifecycleMatchesDate = lifecycle.business_date === status.working_business_date;
  const lifecycleStages = [
    { label: "Reset đầu ngày", ...lifecycle.morning_reset },
    { label: "Snapshot buổi chiều", ...lifecycle.afternoon_snapshot },
    { label: "Đối soát AI", ...lifecycle.evening_reconcile },
  ];
  const lifecycleIssues = usesV4Slots ? lifecycleStages.filter(stage =>
    ["failed", "blocked", "review_required", "stale", "retryable_failure", "permanent_failure", "terminal_failure"].includes(stage.status)
    || Boolean(stage.error_code),
  ) : [];
  const invariants = status.invariants || [];
  const v4NeedsRecovery = lifecycleIssues.length > 0 || invariants.length > 0 || !lifecycleMatchesDate;
  const v4Completed = Boolean(status.lifecycle) && lifecycleMatchesDate && !v4NeedsRecovery
    && lifecycleStages.every(stage => stage.status === "completed")
    && lifecycle.evening_reconcile.verified;
  const hasReconciliationRows = typeof summary.row_count === "number" && Number.isFinite(summary.row_count);
  const reconciliationVerified = reconciliation?.status === "completed" && hasReconciliationRows
    && typeof summary.invalid_count === "number" && summary.invalid_count === 0;
  const automationReady = status.enabled && status.configured && healthy;
  const hasCurrentCycleEvidence = usesV4Slots ? v4Completed : Boolean(run && run.business_date === status.working_business_date);
  const hasExceptions = blockers.length > 0 || v4NeedsRecovery || !automationReady;
  const v4Started = [lifecycle.morning_reset.status, lifecycle.afternoon_snapshot.status, lifecycle.evening_reconcile.status]
    .some(stage => !["pending", "scheduled"].includes(stage));
  const runLabel = run?.finalized ? "Đã chốt" : run?.ready ? "Sẵn sàng" : run ? "Cần xử lý" : usesV4Slots ? (v4Completed ? "Đã hoàn tất" : v4NeedsRecovery ? "Cần recovery" : v4Started ? "Đang xử lý" : "Chưa chạy") : "Chưa tạo";
  const runTone = run?.finalized || run?.ready || (usesV4Slots && v4Completed) ? "success" : hasExceptions ? "warning" : v4Started ? "running" : "muted";

  useEffect(() => {
    if (!modalPage) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setModalPage(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [modalPage]);

  return <div className="ops-content ops-inventory-daily">
    <WorkspacePageHeader route="inventory" title="Tổng quan kiểm kê tự động" eyebrow="Inventory · Operations"
      className="cam-header-v18 cam-inventory-header-v18"
      description={<>Trạng thái chu kỳ, các bước chạy nền và việc cần xử lý · {status.timezone}</>}
      titleAddon={<span className={"ops-inventory-health " + (healthy ? "healthy" : status.operational_state)}>{operationalLabel(status.operational_state)}</span>}
      actions={<div className="ops-inventory-heading-actions">
        {status.working_spreadsheet_url ? <a className="ops-inventory-source-link" href={status.working_spreadsheet_url} target="_blank" rel="noreferrer"><QuickActionIcon name="sheet" />Mở bảng kiểm kho</a> : null}
        <button className="ops-inventory-refresh-action" type="button" onClick={onRefresh}><QuickActionIcon name="refresh" />Làm mới</button>
        <WorkspaceBackToAssets />
      </div>} />

    {refreshError && <div className="ops-inventory-transient-error" role="status">
      <div><strong>Chưa làm mới được Inventory</strong><p>Đang giữ dữ liệu gần nhất. {refreshError}</p></div>
      <button type="button" onClick={onRefresh}>Thử lại</button>
    </div>}

    <section className="ops-inventory-summary ops-inventory-summary--compact" aria-label="Tóm tắt Inventory hằng ngày">
      <article className="ops-inventory-summary-card processing-date">
        <span>Ngày kiểm kê</span>
        <strong><time dateTime={status.working_business_date}>{businessDate(status.working_business_date)}</time></strong>
        <small>Giờ {status.timezone} · hôm nay {businessDate(status.current_local_date)}</small>
      </article>
      <article className={"ops-inventory-summary-card run " + runTone}>
        <span>Trạng thái chu kỳ</span>
        <strong>{runLabel}</strong>
        <small>{run ? "Ngày " + businessDate(run.business_date) + " · " + processStatusLabel(run.status) : usesV4Slots ? "Theo dõi chu kỳ " + businessDate(status.working_business_date) : "Chưa có daily run cho ngày " + businessDate(status.working_business_date)}</small>
      </article>
      <article className={"ops-inventory-summary-card result " + (reconciliationVerified ? "success" : reconciliation ? "warning" : "muted")}>
        <span>Đối soát gần nhất</span>
        <strong>{hasReconciliationRows ? number(summary.row_count) + " dòng" : "Chưa có dữ liệu"}</strong>
        <small>{reconciliation ? number(summary.changed_count) + " thay đổi · " + number(summary.invalid_count) + " không hợp lệ" : "Chưa có kết quả đối soát để đánh giá."}</small>
      </article>
    </section>

    <section className="ops-inventory-timeline" aria-label="Ba bước xử lý Inventory">
      <header className="ops-inventory-timeline-heading">
        <h3>Quy trình trong ngày</h3>
        <span className={automationReady ? "is-ready" : "is-warning"}>
          {automationReady ? "Lịch chạy nền đã cấu hình · không cần mở trang" : "Lịch xử lý đang tắt hoặc chưa đủ cấu hình"}
        </span>
      </header>
      <div className="ops-inventory-steps">
        <article className={"ops-inventory-step " + lifecycle.morning_reset.status}>
          <span className="ops-inventory-step-number" aria-hidden="true">01</span>
          <div className="ops-inventory-step-body">
            <div className="ops-inventory-step-title">
              <h4>Reset đầu ngày</h4>
              <span className={"ops-inventory-card-status " + (lifecycle.morning_reset.status === "completed" ? "success" : "muted")}>{processStatusLabel(lifecycle.morning_reset.status)}</span>
            </div>
            <p><time>{lifecycle.morning_reset.scheduled_time || "—"}</time> · Chuyển tồn đã xác minh từ ngày trước</p>
            <details><summary>Xem chi tiết</summary>
              <dl><div><dt>Ngày nguồn</dt><dd>{businessDate(lifecycle.morning_reset.source_business_date)}</dd></div><div><dt>Lỗi</dt><dd>{lifecycle.morning_reset.error_code || status.carry_forward?.error_code || "Không có"}</dd></div></dl>
            </details>
          </div>
        </article>
        <article className={"ops-inventory-step " + lifecycle.afternoon_snapshot.status}>
          <span className="ops-inventory-step-number" aria-hidden="true">02</span>
          <div className="ops-inventory-step-body">
            <div className="ops-inventory-step-title">
              <h4>Snapshot buổi chiều</h4>
              <span className={"ops-inventory-card-status " + (lifecycle.afternoon_snapshot.status === "completed" ? "success" : "muted")}>{processStatusLabel(lifecycle.afternoon_snapshot.status)}</span>
            </div>
            <p><time>{lifecycle.afternoon_snapshot.scheduled_time || status.snapshot_time || "—"}</time> · Lưu bản sao dữ liệu không sửa nguồn</p>
            <details><summary>Xem chi tiết</summary>
              <dl><div><dt>Ngày dữ liệu</dt><dd>{businessDate(snapshot?.business_date)}</dd></div><div><dt>Hoàn thành</dt><dd>{dateTime(snapshot?.completed_at)}</dd></div><div><dt>Lỗi</dt><dd>{lifecycle.afternoon_snapshot.error_code || snapshot?.error_code || "Không có"}</dd></div></dl>
              <div className="ops-inventory-step-links">
                {snapshot?.snapshot_url ? <a href={snapshot.snapshot_url} target="_blank" rel="noreferrer">Bản Snapshot ↗</a> : null}
                {snapshot?.gemini_url ? <a href={snapshot.gemini_url} target="_blank" rel="noreferrer">Bản đối soát ↗</a> : null}
              </div>
            </details>
          </div>
        </article>
        <article className={"ops-inventory-step " + lifecycle.evening_reconcile.status}>
          <span className="ops-inventory-step-number" aria-hidden="true">03</span>
          <div className="ops-inventory-step-body">
            <div className="ops-inventory-step-title">
              <h4>Đối soát AI</h4>
              <span className={"ops-inventory-card-status " + (lifecycle.evening_reconcile.status === "completed" && lifecycle.evening_reconcile.verified ? "success" : "muted")}>{processStatusLabel(lifecycle.evening_reconcile.status)}</span>
            </div>
            <p><time>{lifecycle.evening_reconcile.scheduled_time || status.reconcile_time || "—"}</time> · {lifecycle.evening_reconcile.verified ? "Đã xác minh kết quả" : "Chưa xác minh kết quả"}</p>
            <details><summary>Xem chi tiết</summary>
              <dl><div><dt>Xác minh</dt><dd>{lifecycle.evening_reconcile.verified ? "Đã xác minh" : "Chưa xác minh"}</dd></div><div><dt>Plan</dt><dd>{lifecycle.evening_reconcile.plan_hash?.slice(0,12) || "—"}</dd></div><div><dt>Lỗi</dt><dd>{lifecycle.evening_reconcile.error_code || "Không có"}</dd></div></dl>
            </details>
          </div>
        </article>
      </div>
    </section>

    {hasExceptions ? <section className="ops-inventory-blockers">
      <h3>Cần kiểm tra chu kỳ hiện tại</h3>
      <p>Hệ thống đã phát hiện các điều kiện chưa hoàn tất. Chỉ xử lý ngoại lệ sau khi đối chiếu bằng chứng.</p>
      <details><summary>Xem lý do</summary><ul>
        {!automationReady && <li>{!status.enabled || !status.configured ? "Lịch xử lý đang tắt hoặc chưa đủ cấu hình." : "Hệ thống đang báo trạng thái cần kiểm tra."}</li>}
        {usesV4Slots && !lifecycleMatchesDate && <li>Trạng thái tiến trình chưa khớp ngày kiểm kê đang theo dõi.</li>}
        {lifecycleIssues.map(stage => <li key={stage.label}><strong>{stage.label}: {processStatusLabel(stage.status)}</strong>{stage.error_code ? <span>{stage.error_code}</span> : null}</li>)}
        {invariants.map((invariant, index) => <li key={invariant.code + "-" + index}>{invariant.code} · {invariant.stage}</li>)}
        {blockers.map((blocker, index) => <li key={blocker.code + "-" + index}><strong>{blocker.code}</strong><span>{(blocker.document_ids?.length || 0)} tài liệu · {(blocker.review_ids?.length || 0)} mục xem xét · {(blocker.job_ids?.length || 0)} tác vụ</span></li>)}
      </ul></details>
    </section> : hasCurrentCycleEvidence ? <div className="ops-inventory-clear"><strong>Không phát hiện ngoại lệ từ chu kỳ hiện tại</strong><span>Đây là trạng thái theo dõi; không tự xác nhận kiểm đếm thực tế hoặc ký chốt ca.</span></div> : <div className="ops-inventory-automation-note" role="status"><strong>Chưa đủ dữ liệu để kết luận chu kỳ hiện tại</strong><span>Các bước chưa hoàn tất hoặc chưa được xác minh. Tiếp tục theo dõi trạng thái xử lý.</span></div>}

    <details className="ops-inventory-human-actions">
      <summary>Công việc cần nhân viên hoặc quản lý xác nhận</summary>
      <p>Chỉ mở khi thực sự có kiểm đếm vật lý, biên bản cần duyệt, bàn giao kho hoặc chứng từ chưa rõ. Những việc này không được tự xác nhận thay con người theo Q01–Q47.</p>
      <div className="ops-inventory-human-buttons">
        <button type="button" onClick={() => setModalPage("closing")}>Xác nhận kiểm đếm</button>
        <button type="button" onClick={() => setModalPage("approvals")}>Duyệt ngoại lệ</button>
        <button type="button" onClick={() => setModalPage("transfers")}>Xác nhận bàn giao</button>
        <button type="button" onClick={() => setModalPage("review")}>Xem chứng từ cần duyệt</button>
        <button type="button" onClick={() => setModalPage("materials")}>Đối chiếu vật tư chưa rõ</button>
      </div>
    </details>

    {modalPage ? <div className="ops-inventory-modal-backdrop" role="presentation" onMouseDown={() => setModalPage(null)}>
      <section className="ops-inventory-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-modal-title" onMouseDown={event => event.stopPropagation()}>
        <header><div><span>OPERATIONS</span><h2 id="inventory-modal-title">Inventory</h2></div><button type="button" autoFocus aria-label="Đóng Inventory" onClick={() => setModalPage(null)}>×</button></header>
        <InventoryApp key={modalPage} embedded initialPage={modalPage} showNavigation={false} />
      </section>
    </div> : null}
  </div>;
}
