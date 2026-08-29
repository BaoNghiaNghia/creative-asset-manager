import { useEffect, useState } from "react";

import {
  InventoryApiError,
  inventoryApi,
  inventoryDailySheetApi,
  type InventoryDailyRun,
  type InventoryDailySheetStatus,
} from "../inventory/api";
import { InventoryApp, type InventoryPage } from "../inventory/InventoryApp";

type InventoryDailyState = {
  status: InventoryDailySheetStatus;
  run: InventoryDailyRun | null;
};

const businessDate = (value: string | null | undefined) => {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  return year && month && day ? day + "/" + month + "/" + year : value;
};

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

const number = (value: unknown) => typeof value === "number" ? value.toLocaleString("vi-VN") : "0";

const operationalLabel = (value: InventoryDailySheetStatus["operational_state"]) => ({
  healthy: "Hoạt động bình thường",
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
  } as Record<string, string>)[value] || value;
};

export function InventoryDailyTab() {
  const [value, setValue] = useState<InventoryDailyState | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    inventoryDailySheetApi.getStatus().then(async status => {
      let run: InventoryDailyRun | null = null;
      try {
        run = await inventoryApi.getDailyRun(status.working_business_date);
      } catch (failure) {
        if (!(failure instanceof InventoryApiError) || failure.status !== 404) throw failure;
      }
      if (alive) setValue({ status, run });
    }).catch(failure => {
      if (alive) setError(failure instanceof Error ? failure.message : "Không thể tải dữ liệu Inventory hôm nay.");
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
  }, [reload]);

  if (loading) return <div className="ops-inventory-state" aria-busy="true">Đang tải dữ liệu Inventory hằng ngày…</div>;
  if (error || !value) return <div className="ops-inventory-state ops-inventory-error" role="alert"><strong>Không thể tải Inventory</strong><p>{error}</p><button type="button" onClick={() => setReload(current => current + 1)}>Thử lại</button></div>;
  return <InventoryDailyOverview {...value} onRefresh={() => setReload(current => current + 1)} />;
}

export function InventoryDailyOverview({ status, run, onRefresh = () => undefined }: InventoryDailyState & { onRefresh?: () => void }) {
  const [modalPage, setModalPage] = useState<InventoryPage | null>(null);
  const reconciliation = status.last_reconciliation;
  const snapshot = status.last_snapshot;
  const summary = reconciliation?.summary || {};
  const blockers = run?.blockers || [];
  const healthy = status.operational_state === "healthy";
  const runLabel = run?.finalized ? "Đã chốt" : run?.ready ? "Sẵn sàng" : run ? "Cần xử lý" : "Chưa tạo";
  const runTone = run?.finalized || run?.ready ? "success" : run ? "danger" : "warning";

  useEffect(() => {
    if (!modalPage) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setModalPage(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [modalPage]);

  return <div className="ops-content ops-inventory-daily">
    <div className="ops-section-heading">
      <div><h2>Inventory hằng ngày</h2><p>Theo dõi dữ liệu kiểm kho, lịch tự động và kết quả xử lý theo múi giờ {status.timezone}.</p></div>
      <div className="ops-inventory-heading-actions">
        <span className={"ops-inventory-health " + (healthy ? "healthy" : status.operational_state)}>{operationalLabel(status.operational_state)}</span>
        <div className="ops-inventory-quick-actions">
          {status.working_spreadsheet_url ? <a href={status.working_spreadsheet_url} target="_blank" rel="noreferrer">Mở Google Sheet đang xử lý</a> : null}
          <button type="button" aria-haspopup="dialog" onClick={() => setModalPage("daily")}>Mở Daily Inventory</button>
          <button type="button" aria-haspopup="dialog" onClick={() => setModalPage("settings")}>Cấu hình Inventory</button>
        </div>
        <button type="button" onClick={onRefresh}>Làm mới</button>
      </div>
    </div>

    <section className="ops-inventory-summary" aria-label="Tóm tắt Inventory hằng ngày">
      <article className="ops-inventory-summary-card today">
        <span>Hôm nay</span>
        <strong><time dateTime={status.current_local_date}>{businessDate(status.current_local_date)}</time></strong>
        <small>Ngày hiện tại theo {status.timezone}</small>
      </article>
      <article className="ops-inventory-summary-card processing-date">
        <span>Ngày dữ liệu đang xử lý</span>
        <strong><time dateTime={status.working_business_date}>{businessDate(status.working_business_date)}</time></strong>
        <small>Dữ liệu ngày liền trước (D-1), xử lý vào sáng hôm nay</small>
      </article>
      <article className={"ops-inventory-summary-card run " + runTone}>
        <span>Trạng thái chu kỳ</span>
        <strong>{runLabel}</strong>
        <small>{run ? "Ngày " + businessDate(run.business_date) + " · " + processStatusLabel(run.status) : "Chưa có daily run cho ngày " + businessDate(status.working_business_date)}</small>
      </article>
      <article className={"ops-inventory-summary-card result " + (Number(summary.invalid_count || 0) ? "danger" : "success")}>
        <span>Đối soát gần nhất</span>
        <strong>{number(summary.row_count)} dòng</strong>
        <small>{number(summary.changed_count)} thay đổi · {number(summary.invalid_count)} không hợp lệ</small>
      </article>
    </section>

    <div className="ops-inventory-grid">
      <article className="ops-inventory-card">
        <header><div><span className="ops-inventory-card-kicker">Tự động</span><h3>Lịch xử lý kế tiếp</h3></div><span className={status.enabled ? "ops-inventory-card-status success" : "ops-inventory-card-status muted"}>{status.enabled ? "Đang bật" : "Đang tắt"}</span></header>
        <p>Hai bước chạy độc lập theo giờ Việt Nam và xử lý dữ liệu ngày D-1.</p>
        <dl><div><dt>Chụp dữ liệu lúc {status.snapshot_time}</dt><dd>{dateTime(status.next_snapshot_at)}</dd></div><div><dt>Đối soát lúc {status.reconcile_time}</dt><dd>{dateTime(status.next_reconciliation_at)}</dd></div><div><dt>Múi giờ</dt><dd>{status.timezone}</dd></div></dl>
      </article>
      <article className="ops-inventory-card">
        <header><div><span className="ops-inventory-card-kicker">Bước 1</span><h3>Snapshot gần nhất</h3></div><span className={"ops-inventory-card-status " + (snapshot?.status === "completed" ? "success" : "muted")}>{processStatusLabel(snapshot?.status)}</span></header>
        <p>Bản chụp dữ liệu trước khi hệ thống thực hiện đối soát.</p>
        <dl><div><dt>Ngày dữ liệu</dt><dd>{businessDate(snapshot?.business_date)}</dd></div><div><dt>Hoàn thành lúc</dt><dd>{dateTime(snapshot?.completed_at)}</dd></div><div><dt>Lỗi</dt><dd>{snapshot?.error_code || "Không có"}</dd></div></dl>
        {snapshot?.snapshot_url ? <a href={snapshot.snapshot_url} target="_blank" rel="noreferrer">Mở snapshot ↗</a> : null}
      </article>
      <article className="ops-inventory-card">
        <header><div><span className="ops-inventory-card-kicker">Bước 2</span><h3>Đối soát gần nhất</h3></div><span className={"ops-inventory-card-status " + (reconciliation?.status === "completed" ? "success" : "muted")}>{processStatusLabel(reconciliation?.status)}</span></header>
        <p>So sánh snapshot mới nhất với ngày trước đó và kiểm tra dữ liệu.</p>
        <dl><div><dt>Ngày so sánh</dt><dd>{reconciliation ? businessDate(reconciliation.business_date) + " ↔ " + businessDate(reconciliation.previous_business_date) : "—"}</dd></div><div><dt>Kết quả</dt><dd>{number(summary.row_count)} dòng · {number(summary.changed_count)} đổi · {number(summary.invalid_count)} lỗi</dd></div><div><dt>Hoàn thành lúc</dt><dd>{dateTime(reconciliation?.completed_at)}</dd></div></dl>
      </article>
    </div>

    {blockers.length ? <section className="ops-inventory-blockers"><h3>Cần xử lý ({blockers.length})</h3><p>Các vấn đề dưới đây đang chặn chu kỳ dữ liệu ngày {businessDate(status.working_business_date)}.</p><ul>{blockers.map((blocker, index) => <li key={blocker.code + "-" + index}><strong>{blocker.code}</strong><span>{(blocker.document_ids?.length || 0)} tài liệu · {(blocker.review_ids?.length || 0)} mục xem xét · {(blocker.job_ids?.length || 0)} tác vụ</span></li>)}</ul></section> : <div className="ops-inventory-clear"><strong>Chu kỳ hiện không có vấn đề cần xử lý</strong><span>Dữ liệu ngày {businessDate(status.working_business_date)} không có blocker.</span></div>}

    {modalPage ? <div className="ops-inventory-modal-backdrop" role="presentation" onMouseDown={() => setModalPage(null)}>
      <section className="ops-inventory-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-modal-title" onMouseDown={event => event.stopPropagation()}>
        <header><div><span>OPERATIONS</span><h2 id="inventory-modal-title">Inventory</h2></div><button type="button" autoFocus aria-label="Đóng Inventory" onClick={() => setModalPage(null)}>×</button></header>
        <InventoryApp key={modalPage} embedded initialPage={modalPage} />
      </section>
    </div> : null}
  </div>;
}
