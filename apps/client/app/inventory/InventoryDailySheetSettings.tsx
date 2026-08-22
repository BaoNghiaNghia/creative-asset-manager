import { useEffect, useState } from "react";
import {
  inventoryDailySheetApi,
  type InventoryDailySheetConfiguration,
  type InventoryDailySheetStatus,
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

export function InventoryDailySheetSettings() {
  const [configuration, setConfiguration] = useState(emptyConfiguration);
  const [configJson, setConfigJson] = useState("{}");
  const [status, setStatus] = useState<InventoryDailySheetStatus | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

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

  const execute = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMessage("");
    try {
      await operation();
      setMessage(success);
      await reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Operation failed.");
    } finally {
      setBusy(false);
    }
  };

  const save = () => execute(async () => {
    const parsed = JSON.parse(configJson) as Record<string, unknown>;
    const saved = await inventoryDailySheetApi.updateConfiguration({ ...configuration, config: parsed });
    setConfiguration(saved);
  }, "Daily Google Sheets configuration saved.");

  const confirmRun = (label: string, operation: () => Promise<unknown>) => {
    if (!window.confirm(label)) return;
    void execute(operation, "Operation completed.");
  };

  return <section className="inventory-sheet-settings">
    <div className="inventory-sheet-heading">
      <div><h2>Daily Google Sheets automation</h2><p>Snapshot the working workbook at 05:50, then reconcile D versus D-1 at 07:00.</p></div>
      <span className={status?.enabled ? "inventory-ready" : "inventory-muted"}>{status?.enabled ? "Enabled" : "Disabled"}</span>
    </div>

    <div className="inventory-sheet-grid">
      <label className="inventory-toggle"><input type="checkbox" checked={configuration.image_pipeline_enabled} onChange={(event)=>update("image_pipeline_enabled",event.target.checked)}/> Image pipeline enabled</label>
      <label className="inventory-toggle"><input type="checkbox" checked={configuration.daily_sheet_automation_enabled} onChange={(event)=>update("daily_sheet_automation_enabled",event.target.checked)}/> Daily Sheets automation enabled</label>
      <label>Working spreadsheet file ID<input value={configuration.working_spreadsheet_file_id||""} onChange={(event)=>update("working_spreadsheet_file_id",event.target.value||null)}/></label>
      <label>Archive root folder ID<input value={configuration.archive_root_folder_id||""} onChange={(event)=>update("archive_root_folder_id",event.target.value||null)}/></label>
      <label>Template spreadsheet file ID<input value={configuration.template_spreadsheet_file_id||""} onChange={(event)=>update("template_spreadsheet_file_id",event.target.value||null)}/></label>
      <label>Target spreadsheet file ID<input value={configuration.target_spreadsheet_file_id||""} onChange={(event)=>update("target_spreadsheet_file_id",event.target.value||null)}/></label>
      <label>Snapshot time<input type="time" value={configuration.snapshot_time_local} onChange={(event)=>update("snapshot_time_local",event.target.value)}/></label>
      <label>Reconcile time<input type="time" value={configuration.reconcile_time_local} onChange={(event)=>update("reconcile_time_local",event.target.value)}/></label>
      <label>Timezone<input value={configuration.timezone} onChange={(event)=>update("timezone",event.target.value)}/></label>
    </div>

    <label className="inventory-sheet-json">Mapping and reset configuration (JSON)
      <textarea value={configJson} onChange={(event)=>setConfigJson(event.target.value)} spellCheck={false}/>
    </label>

    <div className="inventory-actions">
      <button disabled={busy} onClick={()=>void save()}>{busy ? "Working..." : "Save configuration"}</button>
      <button disabled={busy} onClick={()=>void execute(()=>inventoryDailySheetApi.validateConfiguration(),"Configuration is valid.")}>Validate</button>
      <button disabled={busy} onClick={()=>void execute(()=>inventoryDailySheetApi.runReconciliation(true),"Dry-run preview completed.")}>Preview reconciliation</button>
    </div>
    <div className="inventory-actions">
      <button disabled={busy} className="danger" onClick={()=>confirmRun("Snapshot and reset the working sheet now?",()=>inventoryDailySheetApi.runSnapshot())}>Run snapshot and reset</button>
      <button disabled={busy} className="danger" onClick={()=>confirmRun("Apply absolute reconciliation writes now?",()=>inventoryDailySheetApi.runReconciliation(false))}>Run reconciliation</button>
      <button disabled={busy||!status?.last_snapshot?.id} onClick={()=>status?.last_snapshot?.id&&confirmRun("Use the latest completed snapshot as the baseline?",()=>inventoryDailySheetApi.setBaseline(status.last_snapshot!.id))}>Set latest baseline</button>
    </div>

    {message ? <p className={message.includes("failed")||message.includes("Unable") ? "inventory-error" : "inventory-ready"}>{message}</p> : null}
    <div className="inventory-sheet-status">
      <article>
        <h3>Schedule and working sheet</h3>
        <dl>
          <div><dt>Operational state</dt><dd>{status?.operational_state || "Loading"}</dd></div>
          <div><dt>Working business date</dt><dd>{status?.working_business_date || "-"}</dd></div>
          <div><dt>Next snapshot</dt><dd>{status?.next_snapshot_at ? new Date(status.next_snapshot_at).toLocaleString() : "-"}</dd></div>
          <div><dt>Next reconciliation</dt><dd>{status?.next_reconciliation_at ? new Date(status.next_reconciliation_at).toLocaleString() : "-"}</dd></div>
        </dl>
        {status?.working_spreadsheet_url ? <a href={status.working_spreadsheet_url} target="_blank" rel="noreferrer">Open working Google Sheet</a> : null}
      </article>
      <article>
        <h3>Latest snapshot</h3>
        <dl>
          <div><dt>Business date</dt><dd>{status?.last_snapshot?.business_date || "-"}</dd></div>
          <div><dt>Status</dt><dd>{status?.last_snapshot?.status || "Not run"}</dd></div>
          <div><dt>Completed</dt><dd>{status?.last_snapshot?.completed_at ? new Date(status.last_snapshot.completed_at).toLocaleString() : "-"}</dd></div>
          <div><dt>Error</dt><dd>{status?.last_snapshot?.error_code || "-"}</dd></div>
        </dl>
        <div className="inventory-actions">
          {status?.last_snapshot?.snapshot_url ? <a href={status.last_snapshot.snapshot_url} target="_blank" rel="noreferrer">Open snapshot</a> : null}
          {status?.last_snapshot?.archive_folder_url ? <a href={status.last_snapshot.archive_folder_url} target="_blank" rel="noreferrer">Open archive folder</a> : null}
        </div>
      </article>
      <article>
        <h3>Latest reconciliation</h3>
        <dl>
          <div><dt>Compared</dt><dd>{status?.last_reconciliation ? `${status.last_reconciliation.business_date} vs ${status.last_reconciliation.previous_business_date || "baseline"}` : "-"}</dd></div>
          <div><dt>Status</dt><dd>{status?.last_reconciliation?.status || "Not run"}</dd></div>
          <div><dt>Rows / changed / invalid</dt><dd>{status?.last_reconciliation ? `${status.last_reconciliation.summary?.row_count ?? 0} / ${status.last_reconciliation.summary?.changed_count ?? 0} / ${status.last_reconciliation.summary?.invalid_count ?? 0}` : "-"}</dd></div>
          <div><dt>Error</dt><dd>{status?.last_reconciliation?.error_code || "-"}</dd></div>
        </dl>
      </article>
    </div>
  </section>;
}
