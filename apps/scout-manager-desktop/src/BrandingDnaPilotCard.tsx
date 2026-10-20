import {useCallback,useEffect,useState} from "react";
import {invoke} from "@tauri-apps/api/core";
import {CircleAlert,FolderSearch,Globe2,Pause,Play,RefreshCcw,ShieldCheck} from "lucide-react";

type AgentStatus={state:string;pid:number|null;version:string;running:boolean;lastError:string|null;};
type CamShop={id:string;displayName:string;platform:string;shopUrl:string;status:string;accessMode:string};
type CamSync={shops:CamShop[];dispatchState:"disabled";dispatchReason:string};
type ShopBlocker={shopId:string;platform:string;displayName:string;status:"blocked";stagedTasks:number;blockers:string[]};
type DispatchPreview={available:boolean;blockedCount:number;readyCount:number;stagedTasks:number;shops:ShopBlocker[]};
const BLOCKER_LABELS:Record<string,string>={
  scan_task_dispatch_not_deployed:"CAM chưa cấp job",
  marketplace_authorization_not_verified:"Chưa xác minh quyền nền tảng",
  shop_access_not_declared:"Chưa khai báo quyền shop",
  shop_not_active:"Shop chưa kích hoạt",
  no_staged_scan_tasks:"Chưa có task",
  etsy_written_permission_not_verified:"Thiếu quyền truy cập Etsy",
  amazon_agent_policy_review_not_verified:"Chưa duyệt chính sách Amazon",
};
export function describeShopBlockers(blockers:string[]){
 return blockers.map(code=>BLOCKER_LABELS[code]??"Cần kiểm tra: "+code).join(" · ");
}

/** A process can be running without a permitted marketplace scan job. */
export function agentDisplayState(running:boolean,dispatchEnabled:boolean){
 if(!running)return "Agent stopped";
 return dispatchEnabled?"Agent process running":"Agent standby · 0 scan jobs";
}
export function blockedShopSummary(shops:ShopBlocker[]){
 const count=(reason:string)=>shops.filter(shop=>shop.blockers.includes(reason)).length;
 return {
  inactive:count("shop_not_active"),
  undeclared:count("shop_access_not_declared"),
  withoutTasks:count("no_staged_scan_tasks"),
  awaitingSourcePermission:count("marketplace_authorization_not_verified"),
 };
}
const INITIAL:AgentStatus={state:"Checking agent…",pid:null,version:"0.2.6",running:false,lastError:null};

/** An empty Shop Library is still an authenticated, connected CAM session. */
export function camConnectionState(acknowledged:boolean, shops:CamShop[]){
 return {connected:acknowledged,shopCount:acknowledged?shops.length:null};
}

/** Agent-only control plane: all shops, rights, task priorities and dispatch
 * are owned by CAM. No local Shop selector, authorization form or pilot runner. */
export function BrandingDnaPilotCard({nativeAvailable,controllerAvailable,managerBusy}:{
 nativeAvailable:boolean;controllerAvailable:boolean;managerBusy:boolean;
}){
 const [status,setStatus]=useState<AgentStatus>(INITIAL);
 const [shops,setShops]=useState<CamShop[]>([]);
 const [camConnected,setCamConnected]=useState(false);
 const [dispatchReason,setDispatchReason]=useState("scan_task_dispatch_not_deployed");
 const [preview,setPreview]=useState<DispatchPreview|null>(null);
 const [previewError,setPreviewError]=useState("");
 const [camError,setCamError]=useState("");
 const [error,setError]=useState("");
 const [busy,setBusy]=useState(false);
 const [refreshing,setRefreshing]=useState(false);
 const refreshStatus=useCallback(async()=>{
   if(!nativeAvailable)return;
   setStatus(await invoke<AgentStatus>("branding_pilot_status"));
 },[nativeAvailable]);
 const refreshCam=useCallback(async()=>{
   if(!nativeAvailable)return;
   setRefreshing(true);
   try{
     const data=await invoke<CamSync>("branding_pilot_cam_shops");
     if(data.dispatchState!=="disabled"||!Array.isArray(data.shops)){
       throw new Error("CAM job contract changed. Update the Scout Manager before scanning.");
     }
     setShops(data.shops);setCamConnected(true);
     setDispatchReason(data.dispatchReason);setCamError("");
     try {
       const next=await invoke<DispatchPreview>("branding_pilot_dispatch_preview");
       if(next.available && (next.readyCount!==0||next.shops.some(s=>s.status!=="blocked"))){
         throw new Error("CAM dispatch contract changed; scanning remains disabled.");
       }
       setPreview(next);setPreviewError("");
     }catch(e){
       setPreview(null);
       setPreviewError(typeof e==="string"?e:e instanceof Error?e.message:"Preflight unavailable.");
     }
   }catch(e){
     setCamError(typeof e==="string"?e:e instanceof Error?e.message:"CAM connection unavailable.");
     setShops([]);setCamConnected(false);setPreview(null);
   }finally{setRefreshing(false)}
 },[nativeAvailable]);
 useEffect(()=>{
   if(!nativeAvailable)return;
   void refreshStatus().catch(()=>setError("Agent status unavailable."));
   void refreshCam();
   const statusTimer=window.setInterval(()=>{if(!document.hidden)void refreshStatus().catch(()=>undefined)},3000);
   const camTimer=window.setInterval(()=>{if(!document.hidden)void refreshCam()},60000);
   return()=>{window.clearInterval(statusTimer);window.clearInterval(camTimer)};
 },[nativeAvailable,refreshStatus,refreshCam]);
 const disabled=!nativeAvailable||!controllerAvailable||managerBusy||busy;
 const action=async(command:"agent_start"|"stop")=>{
   setBusy(true);setError("");
   try{
     await invoke("branding_pilot_action",{command});
     await refreshStatus();
   }catch(e){setError(typeof e==="string"?e:e instanceof Error?e.message:"Agent command failed")}
   finally{setBusy(false)}
 };
 const amazon=shops.filter(x=>x.platform==="amazon").length;
 const etsy=shops.filter(x=>x.platform==="etsy").length;
 const {connected:acknowledged}=camConnectionState(camConnected,shops);
 const connected=acknowledged&&!camError;
 const dispatchLabel=dispatchReason==="scan_task_dispatch_not_deployed"
   ?"Chưa mở cấp job": "Đang xác minh trạng thái";
 const summary=preview?.available?blockedShopSummary(preview.shops):null;

 return <section className="branding-pilot-panel branding-agent-only" aria-label="Branding DNA Browser Agent">
   <div className="branding-pilot-top">
     <div className="branding-pilot-icon"><Globe2 size={22}/></div>
     <div className="branding-pilot-title">
       <h2>Branding DNA · Browser Agent</h2>
       <p>Shop Library, quyền truy cập và điều phối job được quản lý trên CAM.</p>
     </div>
     <span className="status-pill neutral"><span className="status-dot"/>{agentDisplayState(status.running,false)}</span>
   </div>
   <div className="branding-agent-overview">
     <div><small>Kết nối CAM</small><strong>{connected?"Connected":camError?"Connection error":refreshing?"Connecting…":"Not connected"}</strong></div>
     <div><small>Shop từ CAM</small><strong>{connected?shops.length:"—"} <span>({amazon} Amazon · {etsy} Etsy)</span></strong></div>
     <div><small>Agent</small><strong>{status.running?"Chờ CAM":"Stopped"} <span>· PID {status.pid??"—"}</span></strong></div>
     <div><small>Điều phối</small><strong>{dispatchLabel} <span>· CAM</span></strong></div>
   </div>
   <div className="branding-preflight" aria-label="CAM per-shop scan readiness">
     <div className="branding-preflight-heading">
       <strong>Readiness từng shop</strong>
       <span>{preview?.available
         ? `${preview.blockedCount} blocked · ${preview.stagedTasks} staged · 0 running`
         : connected?"Chờ CAM cung cấp dispatch-preview":"Chưa kết nối"}</span>
     </div>
     {summary&&preview!.shops.length>0&&<small role="status">
       {summary.inactive} shop chưa kích hoạt · {summary.undeclared} chưa khai báo quyền · {summary.withoutTasks} chưa có scan task · {summary.awaitingSourcePermission} chưa xác minh quyền nguồn
     </small>}
     {preview?.available&&preview.shops.length>0&&<details>
       <summary>Xem lý do chưa quét của {preview.shops.length} shop</summary>
       <div className="branding-preflight-list">
         {preview.shops.map(shop=><div key={shop.shopId} className="branding-preflight-shop">
           <strong>{shop.displayName}</strong>
           <span>{shop.platform==="amazon"?"Amazon":"Etsy"} · {shop.stagedTasks} staged</span>
           <small>{describeShopBlockers(shop.blockers)}</small>
         </div>)}
       </div>
     </details>}
     {preview?.available&&preview.shops.length===0&&<small>Chưa có shop Amazon/Etsy trong CAM.</small>}
     {previewError&&<small role="status">Không tải được chi tiết readiness: {previewError}</small>}
   </div>
   <div className="branding-pilot-actions branding-main-actions">
     <button type="button" className="button primary slim" disabled={disabled||status.running||!connected}
       onClick={()=>void action("agent_start")}><Play size={15}/> Start agent</button>
     <button type="button" className="button secondary slim" disabled={disabled||!status.running}
       onClick={()=>void action("stop")}><Pause size={15}/> Stop agent</button>
     <button type="button" className="button secondary slim" disabled={!nativeAvailable} onClick={()=>void invoke("branding_pilot_open_log_folder").catch(e=>setError(typeof e==="string"?e:"Cannot open Agent log folder."))}><FolderSearch size={15}/> Open log folder</button>
     <button type="button" className="button secondary slim" disabled={disabled||refreshing}
       onClick={()=>void refreshCam()}><RefreshCcw size={15}/> {refreshing?"Connecting…":"Refresh CAM"}</button>
   </div>
   <p className="branding-agent-note"><ShieldCheck size={14}/> Agent đọc trạng thái và shop từ CAM, không cần cấu hình shop trên Windows. Backend chưa bật cấp scan job; Start chỉ kết nối và chờ, không tự mở Amazon/Etsy.</p>
   {(camError||error||status.lastError)&&<p role="alert" className="branding-pilot-error"><CircleAlert size={14}/>{error||camError||status.lastError}</p>}
 </section>;
}
