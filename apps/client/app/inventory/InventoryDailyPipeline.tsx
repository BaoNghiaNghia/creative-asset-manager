import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { inventoryLifecycleApi, type InventoryLifecycleHistoryItem, type InventoryLifecycleStage, type InventoryLifecycleStageStatus } from "./api";

const labels: Record<string, string> = { morning_reset: "Reset đầu ngày", afternoon_snapshot: "Đang snapshot", evening_reconcile: "Đang đối soát", verified: "Cần xác minh", completed: "Hoàn tất" };
const symbols: Record<InventoryLifecycleStageStatus, string> = { pending: "○", scheduled: "◌", running: "●", completed: "✓", blocked: "!", review_required: "!", failed: "×", stale: "×" };
type MenuState = { item: InventoryLifecycleHistoryItem; stage: InventoryLifecycleStage; x: number; y: number };

function format(value: string | null) { return value ? new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "—"; }
function Stage({ stage, onClick, onMenu }: { stage: InventoryLifecycleStage; onClick: () => void; onMenu: (event: MouseEvent<HTMLButtonElement>) => void }) {
  return <button type="button" className={`inventory-pipeline-stage ${stage.status}`} onClick={onClick} onContextMenu={onMenu} title={`${stage.label}: ${stage.status}`}><span aria-hidden="true">{symbols[stage.status]}</span><small>{stage.label.replace(" đầu ngày", "")}</small></button>;
}
const errorHints: Record<string, string> = {
  previous_day_gemini_not_verified: "Reset bị chặn vì Gemini của snapshot ngày trước chưa hoàn tất và chưa được xác minh.",
  inventory_gemini_transport_error: "Không kết nối được Gemini trong lần xử lý này. Hãy kiểm tra credential và trạng thái provider trước khi chạy lại.",
};
function Details({ item, onClose }: { item: InventoryLifecycleHistoryItem; onClose: () => void }) {
  return <aside className="inventory-pipeline-details" role="dialog" aria-label="Chi tiết và nhật ký tiến trình">
    <header><div><span>NHẬT KÝ VẬN HÀNH</span><h3>{item.business_date}</h3><p>Giai đoạn hiện tại: <b>{labels[item.current_stage] || item.current_stage}</b></p></div><button onClick={onClose} aria-label="Đóng">×</button></header>
    <div className="inventory-pipeline-detail-list">{item.stages.map((stage) => {
      const message = stage.error_message || (stage.error_code ? errorHints[stage.error_code] : null);
      return <section key={stage.key} className={"inventory-pipeline-detail-stage " + stage.status}>
        <div className="inventory-pipeline-detail-stage-title"><span aria-hidden="true">{symbols[stage.status]}</span><div><b>{stage.label}</b><small>{stage.status}</small></div></div>
        <ol className="inventory-pipeline-log">
          <li><span>Đã lên lịch</span><time>{stage.scheduled_time || "—"}</time></li>
          <li><span>Bắt đầu</span><time>{format(stage.started_at || null)}</time></li>
          <li><span>Hoàn tất</span><time>{format(stage.completed_at || null)}</time></li>
        </ol>
        {stage.error_code ? <div className="inventory-pipeline-log-error"><b>Chi tiết lỗi</b><code>{stage.error_code}</code>{message ? <p>{message}</p> : null}</div> : null}
        {stage.run_id ? <p className="inventory-pipeline-run-id">Run: <code>{stage.run_id}</code></p> : null}
      </section>;
    })}</div>
  </aside>;
}

export function InventoryDailyPipeline({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<{ items: InventoryLifecycleHistoryItem[]; page: number; page_size: number; pages: number } | null>(null);
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(25); const [loading, setLoading] = useState(true); const [error, setError] = useState(false);
  const [selected, setSelected] = useState<InventoryLifecycleHistoryItem | null>(null); const [menu, setMenu] = useState<MenuState | null>(null); const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(async () => { setLoading(true); setError(false); try { setData(await inventoryLifecycleApi.getHistory(page, pageSize)); } catch { setError(true); } finally { setLoading(false); } }, [page, pageSize]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { let timer: number | undefined; const arm = () => { if (timer) window.clearInterval(timer); timer = document.visibilityState === "visible" ? window.setInterval(() => void load(), 30000) : undefined; }; arm(); document.addEventListener("visibilitychange", arm); return () => { if (timer) window.clearInterval(timer); document.removeEventListener("visibilitychange", arm); }; }, [load]);
  useEffect(() => { const close = () => setMenu(null); window.addEventListener("click", close); return () => window.removeEventListener("click", close); }, []);
  const rerun = async () => { if (!menu) return; const { item, stage } = menu; setMenu(null); if (stage.key !== "morning_reset") { setNotice("Chạy lại hiện chỉ áp dụng cho Reset đầu ngày."); return; } if (!window.confirm(`Chạy lại Reset cho ${item.business_date}? Chỉ khả dụng trong cửa sổ ±1 giờ quanh giờ Reset.`)) return; try { const result = await inventoryLifecycleApi.rerunMorningReset(item.business_date); setNotice(`Reset đầu ngày: ${result.status}.`); await load(); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Không thể chạy lại Reset."); } };
  if (loading && !data) return <section className="inventory-empty">Đang tải tiến trình Inventory…</section>;
  if (error && !data) return <section className="inventory-empty"><p>Không thể tải tiến trình.</p><button onClick={() => void load()}>Thử lại</button></section>;
  return <section className={`inventory-pipeline${embedded ? " inventory-pipeline--embedded" : ""}`}><header><div><span>INVENTORY</span><h2>Tiến trình kiểm kho</h2><p>Theo dõi mỗi ngày làm việc từ reset đến xác minh Gemini.</p></div><button onClick={() => void load()} disabled={loading}>{loading ? "Đang làm mới…" : "Làm mới"}</button></header>{error ? <p className="inventory-error">Không thể tải dữ liệu mới nhất.</p> : null}{notice ? <p className="inventory-error">{notice}</p> : null}{!data?.items.length ? <div className="inventory-pipeline-empty"><b>Chưa có tiến trình kiểm kho.</b><span>Tiến trình của ngày làm việc sẽ xuất hiện ở đây khi automation bắt đầu.</span></div> : <div className="inventory-pipeline-scroll"><table><thead><tr><th>NGÀY KIỂM</th><th>FILE</th><th>GIAI ĐOẠN HIỆN TẠI</th><th>LUỒNG XỬ LÝ</th><th>CẬP NHẬT</th><th>CẦN XỬ LÝ</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.business_date} onClick={() => setSelected(item)}><td><b>{new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium" }).format(new Date(`${item.business_date}T00:00:00`))}</b></td><td className="inventory-pipeline-files">{item.files.shared_url ? <a href={item.files.shared_url} target="_blank" rel="noreferrer">Shared ↗</a> : null}{item.files.snapshot_url ? <a href={item.files.snapshot_url} target="_blank" rel="noreferrer">Snapshot ↗</a> : <span>Snapshot: Chưa tạo</span>}{item.files.gemini_url ? <a href={item.files.gemini_url} target="_blank" rel="noreferrer">Gemini ↗</a> : null}</td><td><span className={`inventory-pipeline-chip ${item.overall_status}`}>{labels[item.current_stage] || item.current_stage}</span></td><td><div className="inventory-pipeline-flow">{item.stages.map((stage, index) => <div key={stage.key} className="inventory-pipeline-node"><Stage stage={stage} onClick={() => setSelected(item)} onMenu={(event) => { event.preventDefault(); event.stopPropagation(); setMenu({ item, stage, x: event.clientX, y: event.clientY }); }} />{index < item.stages.length - 1 ? <i className={stage.status === "completed" ? "done" : ""} /> : null}</div>)}</div></td><td>{format(item.updated_at)}</td><td>{item.action_required ? <button className="inventory-pipeline-action" onClick={(event) => { event.stopPropagation(); setSelected(item); }}>{item.action_required.label}</button> : "—"}</td></tr>)}</tbody></table></div>}<footer><label>Số mục mỗi trang <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}>{[25, 50, 100].map((size) => <option key={size}>{size}</option>)}</select></label><div><button disabled={!data || page === 1} onClick={() => setPage(page - 1)}>Trước</button><b>{data?.page || 1} / {data?.pages || 1}</b><button disabled={!data || page >= (data?.pages || 1)} onClick={() => setPage(page + 1)}>Tiếp</button></div></footer>{menu ? <div role="menu" className="inventory-pipeline-context-menu" style={{ left: menu.x, top: menu.y }} onClick={(event) => event.stopPropagation()}><button role="menuitem" onClick={() => { setSelected(menu.item); setMenu(null); }}>Chi tiết</button><button role="menuitem" onClick={() => void rerun()}>Chạy lại</button></div> : null}{selected ? <Details item={selected} onClose={() => setSelected(null)} /> : null}</section>;
}