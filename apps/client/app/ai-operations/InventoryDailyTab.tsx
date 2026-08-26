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

const dateTime = (value: string | null | undefined) => value
  ? new Date(value).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })
  : "—";

const number = (value: unknown) => typeof value === "number" ? value.toLocaleString("vi-VN") : "0";

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
      <div><h2>Inventory hằng ngày</h2><p>Dữ liệu vận hành theo ngày của tenant hiện tại · múi giờ {status.timezone}</p></div>
      <div className="ops-inventory-heading-actions">
        <span className={`ops-inventory-health ${healthy ? "healthy" : status.operational_state}`}>{status.operational_state}</span>
        <button type="button" onClick={onRefresh}>Làm mới</button>
      </div>
    </div>

    <section className="ops-kpis" aria-label="Inventory daily summary">
      <article className="ops-kpi ops-kpi-info"><span>Ngày làm việc</span><strong>{status.working_business_date || "—"}</strong><small>{status.timezone}</small></article>
      <article className={`ops-kpi ${run?.ready ? "ops-kpi-success" : "ops-kpi-warning"}`}><span>Daily run</span><strong>{run?.finalized ? "Đã chốt" : run?.ready ? "Sẵn sàng" : run ? "Còn blocker" : "Chưa tạo"}</strong><small>{run?.status || "Không có run cho ngày này"}</small></article>
      <article className="ops-kpi ops-kpi-info"><span>Số dòng đối soát</span><strong>{number(summary.row_count)}</strong><small>{number(summary.changed_count)} thay đổi</small></article>
      <article className={Number(summary.invalid_count || 0) ? "ops-kpi ops-kpi-danger" : "ops-kpi ops-kpi-success"}><span>Dòng không hợp lệ</span><strong>{number(summary.invalid_count)}</strong><small>{blockers.length} blocker</small></article>
    </section>

    <div className="ops-inventory-grid">
      <article className="ops-inventory-card">
        <h3>Lịch tự động</h3>
        <dl><div><dt>Trạng thái</dt><dd>{status.enabled ? "Đang bật" : "Đang tắt"}</dd></div><div><dt>Snapshot kế tiếp</dt><dd>{dateTime(status.next_snapshot_at)}</dd></div><div><dt>Đối soát kế tiếp</dt><dd>{dateTime(status.next_reconciliation_at)}</dd></div><div><dt>Khung giờ</dt><dd>{status.snapshot_time} / {status.reconcile_time}</dd></div></dl>
      </article>
      <article className="ops-inventory-card">
        <h3>Snapshot gần nhất</h3>
        <dl><div><dt>Ngày</dt><dd>{snapshot?.business_date || "—"}</dd></div><div><dt>Trạng thái</dt><dd>{snapshot?.status || "Chưa chạy"}</dd></div><div><dt>Hoàn thành</dt><dd>{dateTime(snapshot?.completed_at)}</dd></div><div><dt>Lỗi</dt><dd>{snapshot?.error_code || "Không có"}</dd></div></dl>
        {snapshot?.snapshot_url ? <a href={snapshot.snapshot_url} target="_blank" rel="noreferrer">Mở snapshot</a> : null}
      </article>
      <article className="ops-inventory-card">
        <h3>Đối soát gần nhất</h3>
        <dl><div><dt>So sánh</dt><dd>{reconciliation ? `${reconciliation.business_date} / ${reconciliation.previous_business_date || "baseline"}` : "—"}</dd></div><div><dt>Trạng thái</dt><dd>{reconciliation?.status || "Chưa chạy"}</dd></div><div><dt>Dòng / đổi / lỗi</dt><dd>{number(summary.row_count)} / {number(summary.changed_count)} / {number(summary.invalid_count)}</dd></div><div><dt>Hoàn thành</dt><dd>{dateTime(reconciliation?.completed_at)}</dd></div></dl>
      </article>
    </div>

    {blockers.length ? <section className="ops-inventory-blockers"><h3>Cần xử lý ({blockers.length})</h3><ul>{blockers.map((blocker, index) => <li key={`${blocker.code}-${index}`}><strong>{blocker.code}</strong><span>{(blocker.document_ids?.length || 0)} document · {(blocker.review_ids?.length || 0)} review · {(blocker.job_ids?.length || 0)} job</span></li>)}</ul></section> : <p className="ops-inventory-clear">Không có blocker trong daily run hiện tại.</p>}

    <div className="ops-inventory-links">
      {status.working_spreadsheet_url ? <a href={status.working_spreadsheet_url} target="_blank" rel="noreferrer">Mở Google Sheet đang xử lý</a> : null}
      <button type="button" aria-haspopup="dialog" onClick={() => setModalPage("daily")}>Mở Daily Inventory</button>
      <button type="button" aria-haspopup="dialog" onClick={() => setModalPage("settings")}>Cấu hình Inventory</button>
    </div>
    {modalPage ? <div className="ops-inventory-modal-backdrop" role="presentation" onMouseDown={() => setModalPage(null)}>
      <section className="ops-inventory-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-modal-title" onMouseDown={event => event.stopPropagation()}>
        <header><div><span>OPERATIONS</span><h2 id="inventory-modal-title">Inventory</h2></div><button type="button" autoFocus aria-label="Đóng Inventory" onClick={() => setModalPage(null)}>×</button></header>
        <InventoryApp key={modalPage} embedded initialPage={modalPage} />
      </section>
    </div> : null}
  </div>;
}
