import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { CircleAlert, FileCog, FolderSearch, Globe2, Pause, Play, Settings2, ShieldCheck, Wrench } from "lucide-react";

type PilotStatus = {
  state:string;
  pid:number|null;
  version:string;
  configured:boolean;
  setupAvailable:boolean;
  running:boolean;
  lastError:string|null;
};
const INITIAL:PilotStatus={state:"Checking…",pid:null,version:"0.2.0",configured:false,setupAvailable:false,running:false,lastError:null};

export function BrandingDnaPilotCard({
 nativeAvailable, controllerAvailable, managerBusy, onSelectLogs,
}: {
 nativeAvailable:boolean;
 controllerAvailable:boolean;
 managerBusy:boolean;
 onSelectLogs:()=>void;
}) {
 const [status,setStatus]=useState<PilotStatus>(INITIAL);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const refresh=useCallback(async()=>{
   if(!nativeAvailable)return;
   const result=await invoke<PilotStatus>("branding_pilot_status");
   setStatus(result);
 },[nativeAvailable]);
 useEffect(()=>{
   let active=true;
   if(!nativeAvailable)return;
   void refresh().catch(()=>{if(active)setError("Cannot read Branding DNA pilot status.");});
   const interval=window.setInterval(()=>{
     if(!document.hidden)void refresh().catch(()=>undefined);
   },3000);
   return ()=>{active=false;window.clearInterval(interval)};
 },[nativeAvailable,refresh]);
 const disabled=!nativeAvailable||!controllerAvailable||managerBusy||busy;
 const act=async(command:"install"|"check"|"run"|"stop"|"config")=>{
   if(command==="run" && !window.confirm(
     "Run an Amazon/Etsy browser pilot for the exact shop in your config? Confirm the required marketplace permissions have been reviewed. This is NOT a full shop scan."
   ))return;
   setBusy(true);setError("");
   try{
     if(command==="config") await invoke("branding_pilot_edit_config");
     else await invoke("branding_pilot_action",{command});
     await refresh();
     if(command!=="config")onSelectLogs();
   }catch(e){
     setError(typeof e==="string"?e:e instanceof Error?e.message:"Branding DNA action failed");
   }finally{setBusy(false)}
 };
 return <section className="branding-pilot-panel" aria-label="Branding DNA Amazon Etsy browser agent">
   <div className="branding-pilot-top">
     <div className="branding-pilot-icon"><Globe2 size={24}/></div>
     <div className="branding-pilot-title">
       <h2>Branding DNA · Browser Agent</h2>
       <p>Amazon + Etsy storefronts · Chromium pilot · separate profiles and logs</p>
     </div>
     <span className={"status-pill "+(status.running?"success":"neutral")}><span className="status-dot"/>{status.state}</span>
   </div>
   <div className="branding-pilot-meta">
     <span>Agent v{status.version}</span>
     <span>PID {status.pid??"—"}</span>
     <span>{status.configured?"Config file present · authorization checked at launch":"Shop config required"}</span>
   </div>
   <p className="branding-pilot-note"><ShieldCheck size={15}/>
     This pilot runs <strong>only when started here</strong>. Global Run automation, auto-restart and Pinterest Gemini jobs do not activate it. Scan results stay local and unverified.
   </p>
   <div className="branding-pilot-actions">
     <button className="button secondary slim" type="button" disabled={disabled||status.running} onClick={()=>void act("install")}><Wrench size={15}/> Install runtime</button>
     <button className="button secondary slim" type="button" disabled={disabled||status.running} onClick={()=>void act("config")}><FileCog size={15}/> Edit shop config</button>
     <button className="button secondary slim" type="button" disabled={disabled||status.running} onClick={()=>void act("check")}><Settings2 size={15}/> Check</button>
     <button className="button primary slim" type="button" disabled={disabled||status.running||!status.configured||!status.setupAvailable} onClick={()=>void act("run")}><Play size={15}/> Run pilot</button>
     <button className="button secondary slim" type="button" disabled={disabled||!status.running} onClick={()=>void act("stop")}><Pause size={15}/> Stop</button>
     <button className="button secondary slim" type="button" onClick={onSelectLogs}><FolderSearch size={15}/> Live logs</button>
   </div>
   {(error||status.lastError)&&<p className="branding-pilot-error" role="alert"><CircleAlert size={14}/>{error||status.lastError}</p>}
   <small className="branding-pilot-footer">Etsy requires express written authorization; Amazon requires Agent Policy compliance. CAPTCHA/login blocks stop the pilot. No seller API, external scraping service or production scan dispatch.</small>
 </section>;
}
