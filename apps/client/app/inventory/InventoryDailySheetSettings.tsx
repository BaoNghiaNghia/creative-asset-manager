import { useEffect, useMemo, useState } from "react";
import {
  inventoryDailySheetApi,
  type InventoryDailySheetConfiguration,
  type InventoryDailySheetDiscovery,
  type InventoryDailySheetStatus,
  type InventoryDailySheetValidation,
} from "./api";

const emptyConfiguration: InventoryDailySheetConfiguration = {
  image_pipeline_enabled: true,
  daily_sheet_automation_enabled: false,
  working_spreadsheet_file_id: null,
  archive_root_folder_id: null,
  template_spreadsheet_file_id: null,
  target_spreadsheet_file_id: null,
  snapshot_time_local: "05:50",
  reconcile_time_local: "07:00",
  timezone: "Asia/Ho_Chi_Minh",
  config: {},
};

const defaultColumns = {
  item_key: "STT",
  name: "Tên Nguyên Liệu / Vật Tư",
  category: "Phân Loại",
  opening: "SL Đầu Ca / Nhận",
  used: "SL Sử Dụng Pha Chế",
  inbound: "Nhập Hàng",
  waste: "SL Huỷ / Hư Hỏng",
  closing: "Tồn Cuối Ca",
};

function newV2Config() {
  return {
    version: 2, mode: "daily_count_sheet",
    source: {
      sheet: "", range: "A1:H1000", header_row: 1,
      item_row: { strategy: "numeric_key", key_column: "STT" },
      columns: defaultColumns, warehouse: "main",
    },
    stock: { authoritative_column: "closing" },
    reset: { mode: "restore_template", ranges: [] },
    reconciliation: { mode: "report_only", targets: [] },
    new_material_policy: "review_required",
  };
}

export function dailySheetActionVisibility(isV2: boolean, reconciliationMode: string) {
  return {
    runReport: isV2 && reconciliationMode === "report_only",
    applyWrites: !isV2 || reconciliationMode === "target_table",
  };
}

export function inventorySettingsMode(config: Record<string, unknown>) {
  if (config?.version === 4 && config?.mode === "gemini_tool_sheet_agent") return "v4";
  if (config?.version === 2 && config?.mode === "daily_count_sheet") return "daily_count_sheet";
  return "legacy";
}

const text = (value: unknown) => String(value ?? "");
const cellIssue = (item: Record<string, unknown>) =>
  [item.item_name, item.row ? `Row ${item.row}` : "", item.cell, item.code, item.raw_value ? `"${item.raw_value}"` : ""].filter(Boolean).join(" · ");

export function InventoryDailySheetSettings() {
  const [configuration, setConfiguration] = useState(emptyConfiguration);
  const [configJson, setConfigJson] = useState("{}");
  const [status, setStatus] = useState<InventoryDailySheetStatus | null>(null);
  const [validation, setValidation] = useState<InventoryDailySheetValidation | null>(null);
  const [discovery, setDiscovery] = useState<InventoryDailySheetDiscovery | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  const reload = async () => {
    const [saved, currentStatus] = await Promise.all([
      inventoryDailySheetApi.getConfiguration(),
      inventoryDailySheetApi.getStatus(),
    ]);
    const value = saved || emptyConfiguration;
    setConfiguration(value);
    setConfigJson(JSON.stringify(value.config || {}, null, 2));
    setStatus(currentStatus);
  };

  useEffect(() => {
    reload().catch((error) => setMessage(error instanceof Error ? error.message : "Unable to load settings."));
  }, []);

  const update = <K extends keyof InventoryDailySheetConfiguration>(key: K, value: InventoryDailySheetConfiguration[K]) =>
    setConfiguration((current) => ({ ...current, [key]: value }));

  const config = configuration.config as any;
  const configurationMode = inventorySettingsMode(config);
  const isV4 = configurationMode === "v4";
  const isV2 = configurationMode === "daily_count_sheet";
  const v2 = isV2 ? config : newV2Config();
  const actionVisibility = dailySheetActionVisibility(isV2, v2.reconciliation.mode);
  const setV2 = (next: Record<string, unknown>) => {
    setConfiguration((current) => ({ ...current, config: next }));
    setConfigJson(JSON.stringify(next, null, 2));
    setValidation(null);
  };
  const updateSource = (key: string, value: unknown) => setV2({ ...v2, source: { ...v2.source, [key]: value } });
  const updateColumn = (key: string, value: string) => setV2({ ...v2, source: { ...v2.source, columns: { ...v2.source.columns, [key]: value } } });
  const updateReset = (key: string, value: unknown) => setV2({ ...v2, reset: { ...v2.reset, [key]: value } });
  const updateReconciliation = (key: string, value: unknown) => setV2({ ...v2, reconciliation: { ...v2.reconciliation, [key]: value } });
  const updateFirstTarget = (key: string, value: unknown) => {
    const current = v2.reconciliation.targets?.[0] || { warehouse: "main", sheet: "", item_key_range: "", quantity_column: "H", unit_column: "I" };
    updateReconciliation("targets", [{ ...current, [key]: value }]);
  };

  const execute = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage("");
    try { await operation(); setMessage(success); await reload(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Operation failed."); }
    finally { setBusy(false); }
  };

  const save = () => execute(async () => {
    const parsed = advanced ? JSON.parse(configJson) as Record<string, unknown> : configuration.config;
    const saved = await inventoryDailySheetApi.updateConfiguration({ ...configuration, config: parsed });
    setConfiguration(saved);
  }, "Daily Google Sheets configuration saved.");

  const scan = async () => {
    if (!configuration.working_spreadsheet_file_id) { setMessage("Working spreadsheet file ID is required."); return; }
    setBusy(true); setMessage("");
    try {
      const found = await inventoryDailySheetApi.discover(configuration.working_spreadsheet_file_id);
      setDiscovery(found);
      const tab = found.tabs.find((item) => item.detected_header_row) || found.tabs[0];
      if (tab) {
        const next = newV2Config() as any;
        next.source.sheet = tab.title;
        next.source.header_row = tab.detected_header_row || 1;
        next.source.columns = { ...defaultColumns, ...tab.candidate_columns };
        setV2(next);
      }
      setMessage("Workbook scan completed. Review detected columns before saving.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Workbook scan failed."); }
    finally { setBusy(false); }
  };

  const validate = async () => {
    setBusy(true); setMessage("");
    try {
      const report = await inventoryDailySheetApi.validateConfiguration();
      setValidation(report);
      setMessage(report.valid ? "Configuration is valid." : "Configuration has blocking errors.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Validation failed."); }
    finally { setBusy(false); }
  };

  const arming = useMemo(() => {
    if (configuration.daily_sheet_automation_enabled && validation?.valid !== false) return "enabled";
    if (validation?.valid) return "ready";
    return "blocked";
  }, [configuration.daily_sheet_automation_enabled, validation]);

  const confirmRun = (label: string, operation: () => Promise<unknown>) => {
    if (window.confirm(label)) void execute(operation, "Operation completed.");
  };

  return <section className="inventory-sheet-settings inventory-settings-redesign">
    <div className="inventory-settings-hero">
      <div><p className="inventory-kicker">THIẾT LẬP INVENTORY</p><h2>Tự động hóa kiểm kho hằng ngày</h2><p>Quản lý file Google Sheet, lịch chạy và quy tắc xử lý Inventory V4.1.</p></div>
      <span className={arming === "blocked" ? "inventory-blocked" : "inventory-ready"}>{arming === "enabled" ? "ĐANG TỰ ĐỘNG" : arming === "ready" ? "SẴN SÀNG BẬT" : "CHƯA SẴN SÀNG"}</span>
    </div>

    <div className="inventory-settings-summary" aria-label="Tóm tắt cấu hình Inventory">
      <article><span>Phiên bản</span><strong>{isV4 ? "Inventory V4.1" : isV2 ? "Kiểm kho hằng ngày" : "Legacy"}</strong><small>{isV4 ? "Gemini Tool Agent" : "Cấu hình tương thích"}</small></article>
      <article><span>File đang dùng</span><strong>{configuration.working_spreadsheet_file_id ? "Đã kết nối" : "Chưa cấu hình"}</strong>{status?.working_spreadsheet_url ? <a href={status.working_spreadsheet_url} target="_blank" rel="noreferrer">Mở Google Sheet ↗</a> : <small>ID được lưu trong Configuration</small>}</article>
      <article><span>Lịch chạy</span><strong>{configuration.snapshot_time_local} · {configuration.reconcile_time_local}</strong><small>Snapshot · Đối soát</small></article>
      <article><span>Múi giờ</span><strong>{configuration.timezone}</strong><small>Áp dụng cho lịch Inventory</small></article>
    </div>

    <section className="inventory-settings-section">
      <div className="inventory-settings-section-heading"><div><h3>Trạng thái hệ thống</h3><p>Bật hoặc tạm dừng từng luồng độc lập.</p></div></div>
      <div className="inventory-setting-switches">
        <label><input type="checkbox" checked={configuration.image_pipeline_enabled} onChange={(event)=>update("image_pipeline_enabled",event.target.checked)}/><span><strong>Pipeline hình ảnh</strong><small>Tự động nhận và xử lý ảnh chứng từ Inventory.</small></span></label>
        <label><input type="checkbox" checked={configuration.daily_sheet_automation_enabled} disabled={!validation?.valid} onChange={(event)=>update("daily_sheet_automation_enabled",event.target.checked)}/><span><strong>Tự động xử lý Google Sheets</strong><small>Chỉ bật được sau khi cấu hình vượt qua kiểm tra an toàn.</small></span></label>
      </div>
    </section>

    <section className="inventory-settings-section">
      <div className="inventory-settings-section-heading"><div><h3>File và lịch chạy</h3><p>Các thiết lập dùng thường xuyên. File Excel phải được chuyển thành Google Sheet native.</p></div></div>
      <div className="inventory-settings-form-grid inventory-settings-file-schedule-grid">
        {!isV4 ? <>
        <label>Chế độ xử lý<select value={configurationMode} onChange={(event)=>event.target.value === "daily_count_sheet" ? setV2(newV2Config()) : setV2({})}><option value="daily_count_sheet">Kiểm kho hằng ngày</option><option value="legacy">Kho/SKU cũ</option></select><small>{isV4 ? "V4.1 là chế độ production; không thể đổi nhầm tại giao diện này." : "Chọn cấu trúc phù hợp với workbook hiện tại."}</small></label>
        </> : null}
        <label className="wide">Google Spreadsheet ID<div className="inventory-settings-inline-field"><input value={configuration.working_spreadsheet_file_id||""} onChange={(event)=>update("working_spreadsheet_file_id",event.target.value||null)}/><button type="button" disabled={busy || !configuration.working_spreadsheet_file_id} onClick={()=>void scan()}>Quét workbook</button></div><small>ID nằm giữa <code>/spreadsheets/d/</code> và <code>/edit</code> trong đường dẫn Google Sheet.</small></label>
        <label>Giờ snapshot<input type="time" value={configuration.snapshot_time_local} onChange={(event)=>update("snapshot_time_local",event.target.value)}/><small>Lưu trạng thái trước ngày làm việc.</small></label>
        <label>Giờ đối soát<input type="time" value={configuration.reconcile_time_local} onChange={(event)=>update("reconcile_time_local",event.target.value)}/><small>Đọc dữ liệu và lập kế hoạch cập nhật.</small></label>
        <label>Múi giờ<input value={configuration.timezone} onChange={(event)=>update("timezone",event.target.value)}/><small>Khuyến nghị: Asia/Ho_Chi_Minh.</small></label>
      </div>
      <details className="inventory-settings-advanced">
        <summary><span>Lưu trữ và template</span><small>Chỉ thay đổi khi dùng archive hoặc khôi phục từ mẫu.</small></summary>
        <div className="inventory-settings-form-grid"><label>Archive root folder ID<input value={configuration.archive_root_folder_id||""} onChange={(event)=>update("archive_root_folder_id",event.target.value||null)}/><small>Thư mục chứa snapshot lịch sử.</small></label><label>Template Spreadsheet ID<input value={configuration.template_spreadsheet_file_id||""} onChange={(event)=>update("template_spreadsheet_file_id",event.target.value||null)}/><small>Chỉ dùng cho chế độ khôi phục từ template.</small></label></div>
      </details>
    </section>

    {isV2 ? <details className="inventory-settings-advanced inventory-mapping-section" open>
      <summary><span>Ánh xạ dữ liệu kiểm kho</span><small>Chỉ định sheet, vùng đọc và ý nghĩa từng cột.</small></summary>
      <div className="inventory-settings-form-grid">
        <label>Sheet / tab<select value={text(v2.source.sheet)} onChange={(event)=>updateSource("sheet",event.target.value)}>{discovery?.tabs.map((tab)=><option key={tab.sheet_id} value={tab.title}>{tab.title}</option>)}{!discovery?.tabs.length ? <option value={text(v2.source.sheet)}>{text(v2.source.sheet)||"Quét workbook trước"}</option> : null}</select></label>
        <label>Vùng dữ liệu<input value={text(v2.source.range)} onChange={(event)=>updateSource("range",event.target.value)}/><small>Ví dụ: A1:H1000.</small></label>
        <label>Dòng tiêu đề<input type="number" min={1} value={Number(v2.source.header_row)} onChange={(event)=>updateSource("header_row",Number(event.target.value))}/></label>
        <label>Kho<input value={text(v2.source.warehouse)} onChange={(event)=>updateSource("warehouse",event.target.value)}/></label>
        {Object.entries({item_key:"Mã dòng",name:"Tên nguyên liệu",category:"Phân loại",opening:"Đầu ca",used:"Đã dùng",inbound:"Nhập hàng",waste:"Huỷ / hỏng",closing:"Tồn cuối ca"}).map(([key,label])=><label key={key}>Cột {label}<select value={text(v2.source.columns[key])} onChange={(event)=>updateColumn(key,event.target.value)}>{(discovery?.tabs.find((tab)=>tab.title===v2.source.sheet)?.headers || Object.values(defaultColumns)).map((header)=><option key={header} value={header}>{header}</option>)}</select></label>)}
        <label>Cách chuẩn bị ngày mới<select value={v2.reset.mode} onChange={(event)=>updateReset("mode",event.target.value)}><option value="restore_template">Khôi phục từ template</option><option value="clear_entry_columns">Xoá các cột nhập liệu</option><option value="carry_forward">Chuyển tồn cuối → đầu ca</option></select></label>
        {v2.reset.mode === "restore_template" ? <label>Vùng khôi phục<input placeholder="'Sheet'!D2:H1000" value={(v2.reset.ranges||[]).join(", ")} onChange={(event)=>updateReset("ranges",event.target.value.split(",").map((item)=>item.trim()).filter(Boolean))}/></label> : null}
        {v2.reset.mode === "clear_entry_columns" ? <label>Cột cần xoá<input value={(v2.reset.entry_columns||[]).join(", ")} placeholder="used, inbound, waste, closing" onChange={(event)=>updateReset("entry_columns",event.target.value.split(",").map((item)=>item.trim()).filter(Boolean))}/></label> : null}
        {v2.reset.mode === "carry_forward" ? <label>Cột xoá sau carry-forward<input value={(v2.reset.clear_columns||[]).join(", ")} placeholder="used, inbound, waste, closing" onChange={(event)=>updateReset("clear_columns",event.target.value.split(",").map((item)=>item.trim()).filter(Boolean))}/></label> : null}
        <label>Nguyên liệu mới<select value={v2.new_material_policy || "review_required"} onChange={(event)=>setV2({...v2,new_material_policy:event.target.value})}><option value="review_required">Yêu cầu duyệt</option><option value="auto_register_high_confidence">Tự ghi nhận khi ≥98%</option><option value="ignore">Bỏ qua mục chưa rõ</option><option value="block">Chặn đối soát</option></select></label>
        <label>Chế độ đối soát<select value={v2.reconciliation.mode} onChange={(event)=>updateReconciliation("mode",event.target.value)}><option value="report_only">Chỉ lập báo cáo</option><option value="target_table">Ghi vào bảng đích</option></select></label>
        {v2.reconciliation.mode === "target_table" ? <><label>Spreadsheet đích<input value={text(v2.reconciliation.target_spreadsheet_file_id)} onChange={(event)=>updateReconciliation("target_spreadsheet_file_id",event.target.value)}/></label><label>Sheet đích<input value={text(v2.reconciliation.targets?.[0]?.sheet)} onChange={(event)=>updateFirstTarget("sheet",event.target.value)}/></label><label>Vùng mã nguyên liệu<input placeholder="'Kho'!A2:A500" value={text(v2.reconciliation.targets?.[0]?.item_key_range)} onChange={(event)=>updateFirstTarget("item_key_range",event.target.value)}/></label><label>Cột số lượng<input value={text(v2.reconciliation.targets?.[0]?.quantity_column || "H")} onChange={(event)=>updateFirstTarget("quantity_column",event.target.value)}/></label><label>Cột đơn vị<input value={text(v2.reconciliation.targets?.[0]?.unit_column || "I")} onChange={(event)=>updateFirstTarget("unit_column",event.target.value)}/></label></> : null}
      </div>
      <p className="inventory-settings-note">Tồn cuối ca là số liệu tồn kho chuẩn. Carry-forward không bao giờ được bật ngầm.</p>
    </details> : null}

    <section className="inventory-settings-section inventory-settings-actions-card">
      <div className="inventory-settings-section-heading"><div><h3>Lưu và kiểm tra</h3><p>Luôn kiểm tra cấu hình trước khi bật tự động.</p></div></div>
      <div className="inventory-actions"><button disabled={busy} onClick={()=>void save()}>{busy ? "Đang xử lý…" : "Lưu cấu hình"}</button><button disabled={busy} className="secondary" onClick={()=>void validate()}>Kiểm tra an toàn</button><button disabled={busy} className="secondary" onClick={()=>void execute(()=>inventoryDailySheetApi.runReconciliation(true),"Đã tạo bản xem trước; không ghi Google Sheet.")}>Xem trước báo cáo</button>{actionVisibility.runReport ? <button disabled={busy} className="secondary" onClick={()=>void execute(()=>inventoryDailySheetApi.runReconciliation(false),"Đã tạo báo cáo; không ghi bảng đích.")}>Chạy báo cáo</button> : null}</div>
      {message ? <p className={message.includes("failed")||message.includes("Unable")||message.includes("blocking") ? "inventory-error" : "inventory-ready"} role="status">{message}</p> : null}
    </section>

    {discovery ? <section className="inventory-scan-results"><h3>Kết quả quét workbook</h3>{discovery.tabs.map(tab=><article key={tab.sheet_id}><b>{tab.title}: {tab.item_count} nguyên liệu</b><small>{tab.new_material_candidates.length} mới / {tab.possible_renames.length} có thể đổi tên / {tab.anomalies.length} chưa rõ</small>{tab.new_material_candidates.map((item,index)=><p className="inventory-warning" key={"new-"+index}>MỚI / {text(item.name)} / mã sheet {text(item.item_key)}</p>)}{tab.possible_renames.map((item,index)=><p className="inventory-warning" key={"rename-"+index}>Có thể đổi tên / {text(item.name)} / mã sheet {text(item.item_key)}</p>)}</article>)}</section> : null}
    {discovery?.warnings.map((warning,index)=><p className="inventory-warning" key={index}>{cellIssue(warning)}</p>)}
    {validation?.errors.map((error,index)=><p className="inventory-error" key={index}>{cellIssue(error)}</p>)}
    {validation?.warnings.map((warning,index)=><p className="inventory-warning" key={index}>{cellIssue(warning)}</p>)}

    <section className="inventory-settings-section">
      <div className="inventory-settings-section-heading"><div><h3>Hoạt động gần nhất</h3><p>Theo dõi lịch tự động mà không cần mở log kỹ thuật.</p></div>{status?.working_spreadsheet_url ? <a href={status.working_spreadsheet_url} target="_blank" rel="noreferrer">Mở Google Sheet ↗</a> : null}</div>
      <div className="inventory-sheet-status">
        <article><h3>Lịch và ngày làm việc</h3><dl><div><dt>Trạng thái</dt><dd>{status?.operational_state || "Đang tải"}</dd></div><div><dt>Ngày làm việc</dt><dd>{status?.working_business_date || "—"}</dd></div><div><dt>Snapshot kế tiếp</dt><dd>{status?.next_snapshot_at ? new Date(status.next_snapshot_at).toLocaleString("vi-VN") : "—"}</dd></div><div><dt>Đối soát kế tiếp</dt><dd>{status?.next_reconciliation_at ? new Date(status.next_reconciliation_at).toLocaleString("vi-VN") : "—"}</dd></div></dl></article>
        <article><h3>Snapshot gần nhất</h3><dl><div><dt>Ngày</dt><dd>{status?.last_snapshot?.business_date || "—"}</dd></div><div><dt>Kết quả</dt><dd>{status?.last_snapshot?.status || "Chưa chạy"}</dd></div><div><dt>Lỗi</dt><dd>{status?.last_snapshot?.error_code || "Không có"}</dd></div></dl></article>
        <article><h3>Đối soát gần nhất</h3><dl><div><dt>So sánh</dt><dd>{status?.last_reconciliation ? status.last_reconciliation.business_date + " / " + (status.last_reconciliation.previous_business_date || "baseline") : "—"}</dd></div><div><dt>Kết quả</dt><dd>{status?.last_reconciliation?.status || "Chưa chạy"}</dd></div><div><dt>Dòng / đổi / lỗi</dt><dd>{status?.last_reconciliation ? (status.last_reconciliation.summary?.row_count ?? 0) + " / " + (status.last_reconciliation.summary?.changed_count ?? 0) + " / " + (status.last_reconciliation.summary?.invalid_count ?? 0) : "—"}</dd></div></dl></article>
      </div>
    </section>

    <details className="inventory-settings-advanced inventory-settings-danger">
      <summary><span>Thao tác thủ công</span><small>Dành cho xử lý sự cố. Các thao tác ghi luôn yêu cầu xác nhận.</small></summary>
      <p className="inventory-settings-note">Không chạy nếu scheduler đang xử lý. Hệ thống vẫn kiểm tra stale state, protected cells và read-back.</p>
      <div className="inventory-actions"><button disabled={busy} className="secondary" onClick={()=>void execute(()=>inventoryDailySheetApi.runReconciliation(true),"Đã tạo bản xem trước.")}>Xem trước an toàn</button><button disabled={busy} className="danger" onClick={()=>confirmRun("Chạy snapshot và chuẩn bị sheet ngay bây giờ?",()=>inventoryDailySheetApi.runSnapshot())}>Snapshot thủ công</button>{actionVisibility.applyWrites ? <button disabled={busy} className="danger" onClick={()=>confirmRun("Áp dụng các thay đổi đối soát đã được xác minh?",()=>inventoryDailySheetApi.runReconciliation(false))}>Áp dụng đối soát</button> : null}<button disabled={busy||!status?.last_snapshot?.id} className="secondary" onClick={()=>status?.last_snapshot?.id&&confirmRun("Dùng snapshot hoàn tất gần nhất làm baseline?",()=>inventoryDailySheetApi.setBaseline(status.last_snapshot!.id))}>Đặt baseline mới nhất</button></div>
    </details>

    <details open={advanced} onToggle={(event)=>setAdvanced((event.currentTarget as HTMLDetailsElement).open)} className="inventory-sheet-json inventory-settings-advanced">
      <summary><span>Cấu hình JSON nâng cao</span><small>Chỉ dành cho quản trị viên hiểu schema Inventory.</small></summary>
      <textarea value={configJson} onChange={(event)=>setConfigJson(event.target.value)} spellCheck={false}/>
    </details>
  </section>;
}
