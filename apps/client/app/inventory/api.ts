export type InventoryReview = { id:string; document_id:string; line_id:string|null; reason_code:string; status:string; original_value:Record<string, unknown>; suggested_value:Record<string, unknown>; final_value:Record<string, unknown>|null; reviewer_id:string|null; reviewed_at:string|null };
export type InventoryExport = { id:string; business_date:string; status:string; main_drive_file_id:string|null; backup_drive_file_id:string|null; content_sha256:string|null; completed_at:string|null; error_code:string|null; archive_status:string; archive_error_code:string|null };
export type InventoryDailyRun = { id:string; business_date:string; status:string; ready:boolean; finalized:boolean; forced:boolean; blockers:Array<{code:string;document_ids?:string[];review_ids?:string[];job_ids?:string[]}>; report:Record<string,unknown>; finalized_at:string|null; finalized_by:string|null };
export type InventoryAiCredential = { provider:"gemini"; configured:boolean; source:"configuration"|"environment"|"unavailable"; masked_key:string|null; label:string|null; status:string; last_tested_at:string|null; updated_at:string|null; updated_by:string|null };
export type InventoryGeminiCredentialStatus = "VALID"|"INVALID_KEY"|"PERMISSION_DENIED"|"RATE_LIMITED"|"PROVIDER_UNAVAILABLE";
export class InventoryApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "InventoryApiError";
    this.status = status;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}
async function request<T>(path:string, init?:RequestInit):Promise<T> { const response=await fetch(`/api/inventory${path}`,{credentials:"include",headers:{"Content-Type":"application/json",...(init?.headers||{})},...init}); if(!response.ok){let message="Inventory request failed"; try { const body=await response.json(); message=body?.detail?.message||body?.detail?.code||message; } catch {} if (message === "inventory_credential_encryption_unavailable") message="Credential encryption is not configured correctly on the server."; if (message === "inventory_credential_storage_unavailable") message="Credential storage is not ready. A database migration may be required."; throw new InventoryApiError(response.status,message); } return response.json() as Promise<T>; }
export const inventoryApi={ listReviews:()=>request<{items:InventoryReview[]}>("/reviews"), getReview:(id:string)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}`), approve:(id:string)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}/approve`,{method:"POST"}), correct:(id:string,values:Record<string,unknown>)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}/correct`,{method:"POST",body:JSON.stringify({values})}), requestReupload:(id:string)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}/request-reupload`,{method:"POST"}), getDailyRun:(businessDate:string)=>request<InventoryDailyRun>(`/daily-runs/${encodeURIComponent(businessDate)}`), finalizeDailyRun:(businessDate:string,force=false,reason?:string)=>request<InventoryDailyRun>(`/daily-runs/${encodeURIComponent(businessDate)}/finalize`,{method:"POST",body:JSON.stringify({force,reason})}), getExport:(businessDate:string)=>request<InventoryExport>(`/exports/${encodeURIComponent(businessDate)}`), exportDay:(businessDate:string)=>request<InventoryExport>(`/exports/${encodeURIComponent(businessDate)}`,{method:"POST"}), getAiCredential:()=>request<InventoryAiCredential>("/configuration/ai-credential"), testAiCredential:(api_key?:string,label?:string)=>request<{provider:"gemini";status:InventoryGeminiCredentialStatus}>("/configuration/ai-credential/test",{method:"POST",body:JSON.stringify(api_key ? {api_key,label} : {})}), replaceAiCredential:(api_key:string,label?:string)=>request<InventoryAiCredential>("/configuration/ai-credential",{method:"PUT",body:JSON.stringify({api_key,label})}) };


export type InventoryDailySheetConfiguration = {
  image_pipeline_enabled:boolean;
  daily_sheet_automation_enabled:boolean;
  working_spreadsheet_file_id:string|null;
  archive_root_folder_id:string|null;
  template_spreadsheet_file_id:string|null;
  target_spreadsheet_file_id:string|null;
  snapshot_time_local:string;
  reconcile_time_local:string;
  timezone:string;
  config:Record<string,unknown>;
};
export type InventoryDailySheetStatus = {
  enabled:boolean;
  configured:boolean;
  operational_state:"disabled"|"healthy"|"degraded";
  image_pipeline_enabled:boolean;
  timezone:string;
  working_business_date:string;
  snapshot_time:string;
  reconcile_time:string;
  next_snapshot_at:string;
  next_reconciliation_at:string;
  working_spreadsheet_url:string|null;
  last_snapshot:null|{
    id:string;
    business_date:string;
    status:string;
    snapshot_file_id:string|null;
    snapshot_url:string|null;
    archive_folder_url:string|null;
    error_code:string|null;
    completed_at:string|null;
  };
  last_reconciliation:null|{
    id:string;
    business_date:string;
    previous_business_date:string|null;
    status:string;
    summary:Record<string,unknown>;
    error_code:string|null;
    completed_at:string|null;
  };
};
export const inventoryDailySheetApi = {
  getConfiguration:()=>request<InventoryDailySheetConfiguration|null>("/daily-sheet/configuration"),
  updateConfiguration:(body:InventoryDailySheetConfiguration)=>request<InventoryDailySheetConfiguration>("/daily-sheet/configuration",{method:"PUT",body:JSON.stringify(body)}),
  getStatus:()=>request<InventoryDailySheetStatus>("/daily-sheet/status"),
  validateConfiguration:()=>request<{valid:boolean;checks:Array<Record<string,unknown>>}>("/daily-sheet/validate-config",{method:"POST"}),
  runSnapshot:(business_date?:string)=>request<Record<string,unknown>>("/daily-sheet/snapshot/run",{method:"POST",body:JSON.stringify({business_date:business_date||null})}),
  runReconciliation:(dry_run:boolean,business_date?:string)=>request<Record<string,unknown>>("/daily-sheet/reconcile/run",{method:"POST",body:JSON.stringify({business_date:business_date||null,dry_run})}),
  setBaseline:(snapshot_id:string)=>request<Record<string,unknown>>("/daily-sheet/baseline",{method:"POST",body:JSON.stringify({snapshot_id})}),
};
