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
  const isV2 = config?.version === 2 && config?.mode === "daily_count_sheet";
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
    if (configuration.daily_sheet_automation_enabled && validation?.valid !== false) return "AUTOMATION ENABLED";
    if (validation?.valid) return "AUTOMATION READY";
    return "AUTOMATION BLOCKED";
  }, [configuration.daily_sheet_automation_enabled, validation]);

  const confirmRun = (label: string, operation: () => Promise<unknown>) => {
    if (window.confirm(label)) void execute(operation, "Operation completed.");
  };

  return <section className="inventory-sheet-settings">
    <div className="inventory-sheet-heading">
      <div><h2>Daily Google Sheets automation</h2><p>Snapshot at 05:50, then compare immutable D and D-1 snapshots at 07:00.</p></div>
      <span className={arming === "AUTOMATION BLOCKED" ? "inventory-blocked" : "inventory-ready"}>{arming}</span>
    </div>

    <div className="inventory-sheet-grid">
      <label className="inventory-toggle"><input type="checkbox" checked={configuration.image_pipeline_enabled} onChange={(event)=>update("image_pipeline_enabled",event.target.checked)}/> Image pipeline enabled</label>
      <label className="inventory-toggle"><input type="checkbox" checked={configuration.daily_sheet_automation_enabled} disabled={!validation?.valid} onChange={(event)=>update("daily_sheet_automation_enabled",event.target.checked)}/> Daily Sheets automation enabled</label>
      <label>Mode<select value={isV2 ? "daily_count_sheet" : "legacy"} onChange={(event)=>event.target.value === "daily_count_sheet" ? setV2(newV2Config()) : setV2({})}><option value="legacy">Legacy warehouse/SKU</option><option value="daily_count_sheet">Daily count sheet</option></select></label>
      <label>Working Spreadsheet<input value={configuration.working_spreadsheet_file_id||""} onChange={(event)=>update("working_spreadsheet_file_id",event.target.value||null)}/></label>
      <label>Archive root folder ID<input value={configuration.archive_root_folder_id||""} onChange={(event)=>update("archive_root_folder_id",event.target.value||null)}/></label>
      <label>Template Spreadsheet<input value={configuration.template_spreadsheet_file_id||""} onChange={(event)=>update("template_spreadsheet_file_id",event.target.value||null)}/></label>
      <label>Snapshot time<input type="time" value={configuration.snapshot_time_local} onChange={(event)=>update("snapshot_time_local",event.target.value)}/></label>
      <label>Reconcile time<input type="time" value={configuration.reconcile_time_local} onChange={(event)=>update("reconcile_time_local",event.target.value)}/></label>
      <label>Inventory timezone<input value={configuration.timezone} onChange={(event)=>update("timezone",event.target.value)}/></label>
    </div>

    <div className="inventory-actions"><button disabled={busy || !configuration.working_spreadsheet_file_id} onClick={()=>void scan()}>Scan Workbook</button></div>

    {isV2 ? <fieldset className="inventory-sheet-v2">
      <legend>Daily count sheet mapping</legend>
      <div className="inventory-sheet-grid">
        <label>Sheet / tab<select value={text(v2.source.sheet)} onChange={(event)=>updateSource("sheet",event.target.value)}>{discovery?.tabs.map((tab)=><option key={tab.sheet_id} value={tab.title}>{tab.title}</option>)}{!discovery?.tabs.length ? <option value={text(v2.source.sheet)}>{text(v2.source.sheet)||"Scan workbook first"}</option> : null}</select></label>
        <label>Source range<input value={text(v2.source.range)} onChange={(event)=>updateSource("range",event.target.value)}/></label>
        <label>Header row<input type="number" min={1} value={Number(v2.source.header_row)} onChange={(event)=>updateSource("header_row",Number(event.target.value))}/></label>
        <label>Warehouse<input value={text(v2.source.warehouse)} onChange={(event)=>updateSource("warehouse",event.target.value)}/></label>
        {Object.entries({item_key:"Item key",name:"Name",category:"Category",opening:"Opening",used:"Used",inbound:"Inbound",waste:"Waste",closing:"Closing"}).map(([key,label])=>
          <label key={key}>{label} column<select value={text(v2.source.columns[key])} onChange={(event)=>updateColumn(key,event.target.value)}>{(discovery?.tabs.find((tab)=>tab.title===v2.source.sheet)?.headers || Object.values(defaultColumns)).map((header)=><option key={header} value={header}>{header}</option>)}</select></label>
        )}
        <label>Reset mode<select value={v2.reset.mode} onChange={(event)=>updateReset("mode",event.target.value)}><option value="restore_template">Restore from template</option><option value="clear_entry_columns">Clear entry columns</option><option value="carry_forward">Carry forward closing → opening</option></select></label>
        {v2.reset.mode === "restore_template" ? <label>Template restore ranges<input placeholder="'Sheet'!D2:H1000" value={(v2.reset.ranges||[]).join(", ")} onChange={(event)=>updateReset("ranges",event.target.value.split(",").map((item)=>item.trim()).filter(Boolean))}/></label> : null}
        {v2.reset.mode === "clear_entry_columns" ? <label>Entry columns to clear<input value={(v2.reset.entry_columns||[]).join(", ")} placeholder="used, inbound, waste, closing" onChange={(event)=>updateReset("entry_columns",event.target.value.split(",").map((item)=>item.trim()).filter(Boolean))}/></label> : null}
        {v2.reset.mode === "carry_forward" ? <label>Columns cleared after carry-forward<input value={(v2.reset.clear_columns||[]).join(", ")} placeholder="used, inbound, waste, closing" onChange={(event)=>updateReset("clear_columns",event.target.value.split(",").map((item)=>item.trim()).filter(Boolean))}/></label> : null}
        <label>New material policy<select value={v2.new_material_policy || "review_required"} onChange={(event)=>setV2({...v2,new_material_policy:event.target.value})}><option value="review_required">Review required</option><option value="auto_register_high_confidence">Auto-register at 98%+</option><option value="ignore">Ignore unresolved</option><option value="block">Block reconciliation</option></select></label>
        <label>Reconciliation<select value={v2.reconciliation.mode} onChange={(event)=>updateReconciliation("mode",event.target.value)}><option value="report_only">Report only</option><option value="target_table">Target table</option></select></label>
        {v2.reconciliation.mode === "target_table" ? <>
          <label>Target Spreadsheet<input value={text(v2.reconciliation.target_spreadsheet_file_id)} onChange={(event)=>updateReconciliation("target_spreadsheet_file_id",event.target.value)}/></label>
          <label>Target sheet<input value={text(v2.reconciliation.targets?.[0]?.sheet)} onChange={(event)=>updateFirstTarget("sheet",event.target.value)}/></label>
          <label>Target item key range<input placeholder="'Kho'!A2:A500" value={text(v2.reconciliation.targets?.[0]?.item_key_range)} onChange={(event)=>updateFirstTarget("item_key_range",event.target.value)}/></label>
          <label>Target quantity column<input value={text(v2.reconciliation.targets?.[0]?.quantity_column || "H")} onChange={(event)=>updateFirstTarget("quantity_column",event.target.value)}/></label>
          <label>Target unit column<input value={text(v2.reconciliation.targets?.[0]?.unit_column || "I")} onChange={(event)=>updateFirstTarget("unit_column",event.target.value)}/></label>
        </> : null}
      </div>
      <p className="inventory-muted">Closing is the authoritative stock column. Carry-forward is never enabled implicitly.</p>
    </fieldset> : null}

    <details open={advanced} onToggle={(event)=>setAdvanced((event.currentTarget as HTMLDetailsElement).open)} className="inventory-sheet-json"><summary>Advanced raw JSON</summary><textarea value={configJson} onChange={(event)=>setConfigJson(event.target.value)} spellCheck={false}/></details>

    {discovery ? <section className="inventory-scan-results"><h3>Workbook material scan</h3>{discovery.tabs.map(tab=><article key={tab.sheet_id}><b>{tab.title}: {tab.item_count} materials</b><small>{tab.new_material_candidates.length} new / {tab.possible_renames.length} possible renames / {tab.anomalies.length} ambiguous</small>{tab.new_material_candidates.map((item,index)=><p className="inventory-warning" key={"new-"+index}>NEW / {text(item.name)} / sheet key {text(item.item_key)}</p>)}{tab.possible_renames.map((item,index)=><p className="inventory-warning" key={"rename-"+index}>Possible rename / {text(item.name)} / sheet key {text(item.item_key)}</p>)}</article>)}</section> : null}
    {discovery?.warnings.map((warning,index)=><p className="inventory-warning" key={index}>{cellIssue(warning)}</p>)}
    {validation?.errors.map((error,index)=><p className="inventory-error" key={index}>{cellIssue(error)}</p>)}
    {validation?.warnings.map((warning,index)=><p className="inventory-warning" key={index}>{cellIssue(warning)}</p>)}

    <div className="inventory-actions">
      <button disabled={busy} onClick={()=>void save()}>{busy ? "Working..." : "Save configuration"}</button>
      <button disabled={busy} onClick={()=>void validate()}>Validate</button>
      <button disabled={busy} onClick={()=>void execute(()=>inventoryDailySheetApi.runReconciliation(true),"Preview report completed.")}>Preview report</button>
      {actionVisibility.runReport ? <button disabled={busy} onClick={()=>void execute(()=>inventoryDailySheetApi.runReconciliation(false),"Semantic report completed; no Google target writes were performed.")}>Run report</button> : null}
    </div>
    <div className="inventory-actions">
      <button disabled={busy} className="danger" onClick={()=>confirmRun("Snapshot and reset the working sheet now?",()=>inventoryDailySheetApi.runSnapshot())}>Run snapshot and reset</button>
      {actionVisibility.applyWrites ? <button disabled={busy} className="danger" onClick={()=>confirmRun("Apply absolute reconciliation writes now?",()=>inventoryDailySheetApi.runReconciliation(false))}>Apply reconciliation writes</button> : null}
      <button disabled={busy||!status?.last_snapshot?.id} onClick={()=>status?.last_snapshot?.id&&confirmRun("Use the latest completed snapshot as the baseline?",()=>inventoryDailySheetApi.setBaseline(status.last_snapshot!.id))}>Set latest baseline</button>
    </div>

    {message ? <p className={message.includes("failed")||message.includes("Unable")||message.includes("blocking") ? "inventory-error" : "inventory-ready"}>{message}</p> : null}
    <div className="inventory-sheet-status">
      <article><h3>Schedule and working sheet</h3><dl>
        <div><dt>Operational state</dt><dd>{status?.operational_state || "Loading"}</dd></div>
        <div><dt>Working business date</dt><dd>{status?.working_business_date || "-"}</dd></div>
        <div><dt>Next snapshot</dt><dd>{status?.next_snapshot_at ? new Date(status.next_snapshot_at).toLocaleString() : "-"}</dd></div>
        <div><dt>Next reconciliation</dt><dd>{status?.next_reconciliation_at ? new Date(status.next_reconciliation_at).toLocaleString() : "-"}</dd></div>
      </dl>{status?.working_spreadsheet_url ? <a href={status.working_spreadsheet_url} target="_blank" rel="noreferrer">Open working Google Sheet</a> : null}</article>
      <article><h3>Latest snapshot</h3><dl>
        <div><dt>Business date</dt><dd>{status?.last_snapshot?.business_date || "-"}</dd></div>
        <div><dt>Status</dt><dd>{status?.last_snapshot?.status || "Not run"}</dd></div>
        <div><dt>Error</dt><dd>{status?.last_snapshot?.error_code || "-"}</dd></div>
      </dl></article>
      <article><h3>Latest reconciliation</h3><dl>
        <div><dt>Compared</dt><dd>{status?.last_reconciliation ? `${status.last_reconciliation.business_date} vs ${status.last_reconciliation.previous_business_date || "baseline"}` : "-"}</dd></div>
        <div><dt>Status</dt><dd>{status?.last_reconciliation?.status || "Not run"}</dd></div>
        <div><dt>Rows / changed / invalid</dt><dd>{status?.last_reconciliation ? `${status.last_reconciliation.summary?.row_count ?? 0} / ${status.last_reconciliation.summary?.changed_count ?? 0} / ${status.last_reconciliation.summary?.invalid_count ?? 0}` : "-"}</dd></div>
      </dl></article>
    </div>
  </section>;
}
