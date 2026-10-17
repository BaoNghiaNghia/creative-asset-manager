import {useCallback,useEffect,useState} from "react";
import {invoke} from "@tauri-apps/api/core";
import {CircleAlert,FolderSearch,Globe2,Pause,Play,RefreshCcw,ShieldCheck} from "lucide-react";

type AgentStatus={state:string;pid:number|null;version:string;running:boolean;lastError:string|null;};
type CamShop={id:string;displayName:string;platform:string;shopUrl:string;status:string;accessMode:string};
const INITIAL:AgentStatus={state:"Checking agent…",pid:null,version:"0.2.4",running:false,lastError:null};

/** Agent-only control plane: all shops, rights, task priorities and dispatch
 * are owned by CAM. No local Shop selector, authorization form or pilot runner. */
export function BrandingDnaPilotCard({nativeAvailable,controllerAvailable,managerBusy,onSelectLogs}:{
 nativeAvailable:boolean;controllerAvailable:boolean;managerBusy:boolean;onSelectLogs:()=>void;
}){
 const [status,setStatus]=useState<AgentStatus>(INITIAL);
 const [shops,setShops]=useState<CamShop[]>([]);
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
     const data=await invoke<CamShop[]>("branding_pilot_cam_shops");
     setShops(data);setCamError("");
   }catch(e){
     setCamError(typeof e==="string"?e:e instanceof Error?e.message:"CAM connection unavailable.");
     setShops([]);
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
     onSelectLogs();
   }catch(e){setError(typeof e==="string"?e:e instanceof Error?e.message:"Agent command failed")}
   finally{setBusy(false)}
 };
 const amazon=shops.filter(x=>x.platform==="amazon").length;
 const etsy=shops.filter(x=>x.platform==="etsy").length;
 const connected=!camError&&shops.length>0;
 return <section className="branding-pilot-panel branding-agent-only" aria-label="Branding DNA Browser Agent">
   <div className="branding-pilot-top">
     <div className="branding-pilot-icon"><Globe2 size={22}/></div>
     <div className="branding-pilot-title">
       <h2>Branding DNA · Browser Agent</h2>
       <p>Shop Library, quyền truy cập và điều phối job được quản lý trên CAM.</p>
     </div>
     <span className={"status-pill "+(status.running?"success":"neutral")}><span className="status-dot"/>{status.running?"Agent running":"Agent stopped"}</span>
   </div>
   <div className="branding-agent-overview">
     <div><small>Kết nối CAM</small><strong>{connected?"Connected":camError?"Connection error":refreshing?"Connecting…":"Not connected"}</strong></div>
     <div><small>Shop từ CAM</small><strong>{connected?shops.length:"—"} <span>({amazon} Amazon · {etsy} Etsy)</span></strong></div>
     <div><small>Agent</small><strong>{status.running?"Active":"Stopped"} <span>· PID {status.pid??"—"}</span></strong></div>
     <div><small>Điều phối</small><strong>Chưa mở cấp job <span>· CAM</span></strong></div>
   </div>
   <div className="branding-pilot-actions branding-main-actions">
     <button type="button" className="button primary slim" disabled={disabled||status.running||!connected}
       onClick={()=>void action("agent_start")}><Play size={15}/> Start agent</button>
     <button type="button" className="button secondary slim" disabled={disabled||!status.running}
       onClick={()=>void action("stop")}><Pause size={15}/> Stop agent</button>
     <button type="button" className="button secondary slim" onClick={onSelectLogs}><FolderSearch size={15}/> Agent logs</button>
     <button type="button" className="button secondary slim" disabled={disabled||refreshing}
       onClick={()=>void refreshCam()}><RefreshCcw size={15}/> {refreshing?"Connecting…":"Refresh CAM"}</button>
   </div>
   <p className="branding-agent-note"><ShieldCheck size={14}/> Agent đọc trạng thái và shop từ CAM, không cần cấu hình shop trên Windows. Backend chưa bật cấp scan job; Start chỉ kết nối và chờ, không tự mở Amazon/Etsy.</p>
   {(camError||error||status.lastError)&&<p role="alert" className="branding-pilot-error"><CircleAlert size={14}/>{error||camError||status.lastError}</p>}
 </section>;
}
