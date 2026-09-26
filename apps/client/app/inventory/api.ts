export type InventoryReview = { id:string; document_id:string; line_id:string|null; reason_code:string; status:string; original_value:Record<string, unknown>; suggested_value:Record<string, unknown>; final_value:Record<string, unknown>|null; reviewer_id:string|null; reviewed_at:string|null };
export type InventoryExport = { id:string; business_date:string; status:string; main_drive_file_id:string|null; backup_drive_file_id:string|null; content_sha256:string|null; completed_at:string|null; error_code:string|null; archive_status:string; archive_error_code:string|null };
export type InventoryDailyRun = { id:string; business_date:string; status:string; ready:boolean; finalized:boolean; forced:boolean; blockers:Array<{code:string;document_ids?:string[];review_ids?:string[];job_ids?:string[]}>; report:Record<string,unknown>; finalized_at:string|null; finalized_by:string|null };
export type InventoryAiCredential = { provider:"gemini"; configured:boolean; source:"configuration"|"environment"|"unavailable"; masked_key:string|null; label:string|null; status:string; health_status:string; last_test_status:string|null; last_tested_at:string|null; updated_at:string|null; updated_by:string|null };
export type InventoryGeminiCredentialStatus = "VALID"|"INVALID_KEY"|"PERMISSION_DENIED"|"RATE_LIMITED"|"PROVIDER_UNAVAILABLE";
export class InventoryApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly category: string | null;
  readonly retryable: boolean;
  constructor(status: number, message: string, details?: { code?: string | null; category?: string | null; retryable?: boolean }) {
    super(message);
    this.name = "InventoryApiError";
    this.status = status;
    this.code = details?.code ?? null;
    this.category = details?.category ?? null;
    this.retryable = details?.retryable === true;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}
async function request<T>(path:string, init?:RequestInit):Promise<T> { let response:Response; try { response=await fetch(`/api/inventory${path}`,{credentials:"include",headers:{"Content-Type":"application/json",...(init?.headers||{})},...init}); } catch (cause) { throw new InventoryApiError(0,cause instanceof Error ? cause.message : "Inventory network request failed",{code:"inventory_client_transport_error",category:"TRANSPORT",retryable:true}); } if(!response.ok){let message="Inventory request failed"; let code:string|null=null; let category:string|null=null; let retryable=false; try { const body=await response.json(); code=body?.detail?.code||null; category=body?.detail?.error_category||null; retryable=body?.detail?.retryable===true; message=body?.detail?.message||code||message; } catch {} if (message === "inventory_credential_encryption_unavailable") message="Credential encryption is not configured correctly on the server."; if (message === "inventory_credential_storage_unavailable") message="Credential storage is not ready. A database migration may be required."; throw new InventoryApiError(response.status,message,{code,category,retryable}); } return response.json() as Promise<T>; }
export const inventoryApi={ listReviews:()=>request<{items:InventoryReview[]}>("/reviews"), getReview:(id:string)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}`), approve:(id:string)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}/approve`,{method:"POST"}), correct:(id:string,values:Record<string,unknown>)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}/correct`,{method:"POST",body:JSON.stringify({values})}), requestReupload:(id:string)=>request<InventoryReview>(`/reviews/${encodeURIComponent(id)}/request-reupload`,{method:"POST"}), getDailyRun:(businessDate:string)=>request<InventoryDailyRun>(`/daily-runs/${encodeURIComponent(businessDate)}`), finalizeDailyRun:(businessDate:string,force=false,reason?:string)=>request<InventoryDailyRun>(`/daily-runs/${encodeURIComponent(businessDate)}/finalize`,{method:"POST",body:JSON.stringify({force,reason})}), getExport:(businessDate:string)=>request<InventoryExport>(`/exports/${encodeURIComponent(businessDate)}`), exportDay:(businessDate:string)=>request<InventoryExport>(`/exports/${encodeURIComponent(businessDate)}`,{method:"POST"}), getAiCredential:()=>request<InventoryAiCredential>("/configuration/ai-credential"), testAiCredential:(api_key?:string,label?:string)=>request<{provider:"gemini";status:InventoryGeminiCredentialStatus;tested_at:string}>("/configuration/ai-credential/test",{method:"POST",body:JSON.stringify(api_key ? {api_key,label} : {})}), replaceAiCredential:(api_key:string,label?:string)=>request<InventoryAiCredential>("/configuration/ai-credential",{method:"PUT",body:JSON.stringify({api_key,label})}) };


export type InventoryDailySheetConfiguration = {
  image_pipeline_enabled:boolean;
  daily_sheet_automation_enabled:boolean;
  working_spreadsheet_file_id:string|null;
  archive_root_folder_id:string|null;
  template_spreadsheet_file_id:string|null;
  target_spreadsheet_file_id:string|null;
  snapshot_time_local:string;
  reconcile_time_local:string;
  carry_forward_time_local:string;
  timezone:string;
  config:Record<string,unknown>;
};
export type InventoryDailySheetStatus = {
  enabled:boolean;
  configured:boolean;
  execution_mode:"v4_slots"|"legacy_daily_run";
  agent_apply_mode?:string|null;
  operational_state:"disabled"|"healthy"|"degraded";
  image_pipeline_enabled:boolean;
  timezone:string;
  current_local_date:string;
  working_business_date:string;
  snapshot_time:string;
  reconcile_time:string;
  carry_forward_time:string;
  next_snapshot_at:string;
  next_reconciliation_at:string;
  next_carry_forward_at:string;
  working_spreadsheet_url:string|null;
  invariants?:Array<{code:string;stage:string}>;
  last_snapshot:null|{
    id:string;
    business_date:string;
    status:string;
    snapshot_file_id:string|null;
    snapshot_url:string|null;
    gemini_file_id:string|null;
    gemini_url:string|null;
    archive_folder_url:string|null;
    error_code:string|null;
    completed_at:string|null;
    prompt_source?:string|null; prompt_version?:string|null; prompt_hash?:string|null;
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
  carry_forward:null|{
    status:string; target_business_date:string; previous_business_date:string;
    completed_at:string|null; material_count:number; warehouse_count:number;
    issue_count:number; error_code:string|null;
    prompt_source?:string|null; prompt_version?:string|null; prompt_hash?:string|null; source_gemini_file_id?:string|null;
  };
  lifecycle?:{business_date:string;morning_reset:{status:string;scheduled_time:string;source_business_date:string;source_gemini_file_id:string|null;error_code?:string|null;error_message?:string|null;error_category?:string|null;retryable?:boolean;attempt_count?:number|null;max_attempts?:number|null;last_attempt_at?:string|null;next_attempt_at?:string|null;self_healed?:boolean};afternoon_snapshot:{status:string;scheduled_time:string;snapshot_file_id:string|null;gemini_file_id:string|null;error_code?:string|null;error_message?:string|null;error_category?:string|null;retryable?:boolean;attempt_count?:number|null;max_attempts?:number|null;last_attempt_at?:string|null;next_attempt_at?:string|null;self_healed?:boolean};evening_reconcile:{status:string;scheduled_time:string;verified:boolean;run_id:string|null;plan_hash:string|null;prompt_version:string|null;prompt_hash:string|null;error_code?:string|null;error_message?:string|null;error_category?:string|null;retryable?:boolean;attempt_count?:number|null;max_attempts?:number|null;last_attempt_at?:string|null;next_attempt_at?:string|null;self_healed?:boolean}};
  as_of_business_date:string|null;
};
export type InventoryDailySheetValidation = {
  valid:boolean;
  errors:Array<Record<string,unknown>>;
  warnings:Array<Record<string,unknown>>;
  checks:Array<Record<string,unknown>>;
};
export type InventoryGeminiPrompt = {prompt_type:"daily_gemini_processing"|"carry_forward_0900";source:string;version:string;content_hash:string;active_version_id:string|null;active_content:string|null;draft:{id:string;version:number;content:string;content_hash:string;status:string}|null;builtin_content:string;safety_summary:string};
export type InventoryDailySheetDiscovery = {
  spreadsheet_id:string;
  title:string;
  timezone:string;
  warnings:Array<Record<string,unknown>>;
  tabs:Array<{
    title:string;
    sheet_id:number;
    headers:string[];
    detected_header_row:number|null;
    sample_item_rows:Array<Record<string,unknown>>;
    item_count:number;
    materials:Array<Record<string,unknown>>;
    new_material_candidates:Array<Record<string,unknown>>;
    possible_renames:Array<Record<string,unknown>>;
    anomalies:Array<Record<string,unknown>>;
    unit_package_warnings:Array<Record<string,unknown>>;
    row_counts:Record<string,number>;
    formula_presence:boolean;
    candidate_columns:Record<string,string>;
  }>;
};
export const inventoryDailySheetApi = {
  getConfiguration:()=>request<InventoryDailySheetConfiguration|null>("/daily-sheet/configuration"),
  updateConfiguration:(body:InventoryDailySheetConfiguration)=>request<InventoryDailySheetConfiguration>("/daily-sheet/configuration",{method:"PUT",body:JSON.stringify(body)}),
  getStatus:()=>request<InventoryDailySheetStatus>("/daily-sheet/status"),
  validateConfiguration:()=>request<InventoryDailySheetValidation>("/daily-sheet/validate-config",{method:"POST"}),
  discover:(working_spreadsheet_file_id:string)=>request<InventoryDailySheetDiscovery>("/daily-sheet/discover",{method:"POST",body:JSON.stringify({working_spreadsheet_file_id})}),
  runSnapshot:(business_date?:string)=>request<Record<string,unknown>>("/daily-sheet/snapshot/run",{method:"POST",body:JSON.stringify({business_date:business_date||null})}),
  runReconciliation:(dry_run:boolean,business_date?:string)=>request<Record<string,unknown>>("/daily-sheet/reconcile/run",{method:"POST",body:JSON.stringify({business_date:business_date||null,dry_run})}),
  setBaseline:(snapshot_id:string)=>request<Record<string,unknown>>("/daily-sheet/baseline",{method:"POST",body:JSON.stringify({snapshot_id})}),
  getPrompts:()=>request<{prompts:InventoryGeminiPrompt[]}>("/daily-sheet/prompts"),
  getPromptVersions:(type:InventoryGeminiPrompt["prompt_type"])=>request<{versions:Array<Record<string,unknown>>}>(`/daily-sheet/prompts/${encodeURIComponent(type)}/versions`),
  createPromptDraft:(type:InventoryGeminiPrompt["prompt_type"],content:string)=>request<Record<string,unknown>>(`/daily-sheet/prompts/${encodeURIComponent(type)}/drafts`,{method:"POST",body:JSON.stringify({content})}),
  activatePrompt:(type:InventoryGeminiPrompt["prompt_type"],id:string)=>request<Record<string,unknown>>(`/daily-sheet/prompts/${encodeURIComponent(type)}/drafts/${encodeURIComponent(id)}/activate`,{method:"POST"}),
  restorePrompt:(type:InventoryGeminiPrompt["prompt_type"],id:string)=>request<Record<string,unknown>>(`/daily-sheet/prompts/${encodeURIComponent(type)}/versions/${encodeURIComponent(id)}/restore`,{method:"POST"}),
  resetPrompt:(type:InventoryGeminiPrompt["prompt_type"])=>request<Record<string,unknown>>(`/daily-sheet/prompts/${encodeURIComponent(type)}/reset`,{method:"POST"}),
  rerunCurrentGemini:()=>request<Record<string,unknown>>("/daily-sheet/agent-v4/rerun-current",{method:"POST"}),
};


export type InventoryMaterial={material_id:string;canonical_name:string;category:string|null;canonical_dimension:string|null;preferred_unit:string|null;active:boolean;first_seen_at:string|null;last_seen_at:string|null;metadata:Record<string,unknown>;sheet_keys:string[];aliases:string[];package_conversions:Array<{package_name:string;canonical_value:string;canonical_unit:string}>};
export type InventoryMaterialCandidate={id:string;status:"new_material"|"possible_rename"|"ambiguous";sheet:string;source_row:number;sheet_item_key:string;raw_name:string;category:string|null;suggested_item_id:string|null;suggested_canonical_name:string|null;confidence:number;reasons:string[]};
export const inventoryMaterialApi={
  list:()=>request<{items:InventoryMaterial[]}>("/materials"),
  candidates:()=>request<{items:InventoryMaterialCandidate[]}>("/materials/candidates"),
  approve:(id:string,body:{item_id?:string;canonical_name?:string;preferred_unit?:string;canonical_dimension?:string})=>request<InventoryMaterial>(`/materials/candidates/${encodeURIComponent(id)}/approve`,{method:"POST",body:JSON.stringify(body)}),
  ignore:(id:string)=>request<{status:string}>(`/materials/candidates/${encodeURIComponent(id)}/ignore`,{method:"POST"}),
  reject:(id:string)=>request<{status:string}>(`/materials/candidates/${encodeURIComponent(id)}/reject`,{method:"POST"}),
};

export type InventoryLifecycleStageStatus = "pending"|"scheduled"|"running"|"completed"|"blocked"|"review_required"|"failed"|"stale";
export type InventoryLifecycleStage = { key:"morning_reset"|"afternoon_snapshot"|"evening_reconcile"|"verified"; label:string; status:InventoryLifecycleStageStatus; scheduled_time?:string; started_at?:string|null; completed_at?:string|null; error_code?:string|null; error_message?:string|null; error_category?:string|null; retryable?:boolean; attempt_count?:number|null; max_attempts?:number|null; last_attempt_at?:string|null; next_attempt_at?:string|null; self_healed?:boolean; run_id?:string|null; plan_hash?:string|null; prompt_version?:string|null; prompt_hash?:string|null };
export type InventoryLifecycleInvariant = { code:string; stage:string; message:string };
export type InventoryLifecycleHistoryItem = { business_date:string; overall_status:InventoryLifecycleStageStatus; current_stage:string; stages:InventoryLifecycleStage[]; invariants?:InventoryLifecycleInvariant[]; files:{shared_url:string|null;snapshot_url:string|null;gemini_url:string|null}; updated_at:string|null; action_required:{code:string;stage:string;label:string}|null };
export type InventoryLifecycleHistoryResponse = {items:InventoryLifecycleHistoryItem[];page:number;page_size:number;total:number;pages:number};
export type InventoryHistoricalReplayResult = { run_id:string; business_date:string; mode:"fresh_copy"|"existing_copy"; status:string; verification_status:string; promoted:boolean; source_snapshot_file_id:string; previous_gemini_file_id:string|null; replay_gemini_file_id:string; model:string|null; plan_hash:string|null; writes:number; morning_reset_recovery?:{status:string;stage:string;error_code?:string} };

export type InventoryOperationChange = {
  sequence:number;
  sheet:string;
  row_number:number|null;
  cell:string;
  before:unknown;
  after:unknown;
  source_sheet:string|null;
  source_cell:string|null;
  material_id:string|null;
  warehouse_id:string|null;
  operation_type:string;
  reason:string;
  provenance:string|null;
  evidence:Array<Record<string,unknown>>;
  verification_status:string;
};
export type InventoryStageDetail = {
  id:string;
  business_date:string;
  stage:string;
  stage_label:string;
  run_id:string|null;
  status:string;
  summary:Record<string,unknown>;
  assessment:Record<string,unknown>;
  tool_trace:Array<Record<string,unknown>>;
  read_ranges:Array<string|Record<string,unknown>>;
  prompt:{source:string|null;version:string|null;hash:string|null};
  knowledge:{hash:string|null;version:number|null};
  model:string|null;
  writes:number;
  error_code:string|null;
  error_message:string|null;
  started_at:string|null;
  completed_at:string|null;
  changes:InventoryOperationChange[];
  issues?:Array<Record<string,unknown>>;
  source:string;
};
export type InventoryKnowledgeKind = "RULE"|"EXCEPTION"|"COLUMN_MEANING"|"ROW_TYPE"|"FORMULA"|"MATERIAL_MAPPING"|"WAREHOUSE_MAPPING"|"UNIT_CONVERSION"|"NAMING_PATTERN"|"DO_NOT_EDIT"|"BUSINESS_NOTE";
export type InventoryKnowledgeStatus = "proposed"|"draft"|"active"|"archived"|"rejected";
export type InventoryKnowledgeEntry = {
  id:string;
  knowledge_key:string;
  version:number;
  kind:InventoryKnowledgeKind;
  scope_type:string;
  scope_key:string;
  title:string;
  content:string;
  structured_rule:Record<string,unknown>;
  status:InventoryKnowledgeStatus;
  confidence:number|null;
  source:string;
  source_run_id:string|null;
  source_content_hash:string|null;
  evidence:Array<Record<string,unknown>>;
  supersedes_id:string|null;
  created_by:string|null;
  activated_by:string|null;
  created_at:string|null;
  updated_at:string|null;
  activated_at:string|null;
};
export type InventoryKnowledgeDraft = {
  kind:InventoryKnowledgeKind;
  title:string;
  content:string;
  scope_type:string;
  scope_key:string;
  structured_rule:Record<string,unknown>;
  evidence?:Array<Record<string,unknown>>;
  confidence?:number|null;
};

export const inventoryLifecycleApi = {
  getHistory:(page=1,pageSize=25)=>request<InventoryLifecycleHistoryResponse>(`/daily-sheet/lifecycle-history?page=${page}&page_size=${pageSize}`),
  getStageDetail:(businessDate:string,stage:string)=>request<InventoryStageDetail>(`/daily-sheet/lifecycle-history/${encodeURIComponent(businessDate)}/stages/${encodeURIComponent(stage)}/detail`),
  rerunMorningReset:(businessDate:string)=>request<{status:string;stage:string}>(`/daily-sheet/lifecycle-history/${encodeURIComponent(businessDate)}/morning-reset/rerun`,{method:"POST"}),
  replayHistoricalGemini:(businessDate:string,mode:"fresh_copy"|"existing_copy"="fresh_copy",promote=true)=>request<InventoryHistoricalReplayResult>(`/daily-sheet/lifecycle-history/${encodeURIComponent(businessDate)}/gemini/replay`,{method:"POST",body:JSON.stringify({mode,promote})}),
};
export const inventoryKnowledgeApi = {
  list:(status?:string)=>request<{items:InventoryKnowledgeEntry[]}>(`/daily-sheet/knowledge${status?`?status=${encodeURIComponent(status)}`:""}`),
  create:(body:InventoryKnowledgeDraft)=>request<InventoryKnowledgeEntry>("/daily-sheet/knowledge",{method:"POST",body:JSON.stringify(body)}),
  revise:(id:string,body:Partial<InventoryKnowledgeDraft>)=>request<InventoryKnowledgeEntry>(`/daily-sheet/knowledge/${encodeURIComponent(id)}/revisions`,{method:"POST",body:JSON.stringify(body)}),
  activate:(id:string)=>request<InventoryKnowledgeEntry>(`/daily-sheet/knowledge/${encodeURIComponent(id)}/activate`,{method:"POST"}),
  reject:(id:string)=>request<InventoryKnowledgeEntry>(`/daily-sheet/knowledge/${encodeURIComponent(id)}/reject`,{method:"POST"}),
};
