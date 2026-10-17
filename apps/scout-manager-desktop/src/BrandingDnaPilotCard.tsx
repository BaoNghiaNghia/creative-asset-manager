import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { BrandingDnaReports } from "./BrandingDnaReports";
import { CircleAlert, FileCog, FolderSearch, Globe2, Pause, Play, Plus, Save, Settings2, ShieldCheck, Wrench } from "lucide-react";

type PilotStatus = {
  state:string; pid:number|null; version:string; configured:boolean;
  setupAvailable:boolean; running:boolean; lastError:string|null;
  approvalFilePresent:boolean;
};
type SavedShop = {id:string;platform:string;shopUrl:string;active:boolean;approvalFilePresent:boolean};
type CamShop = {id:string;displayName:string;platform:string;shopUrl:string;status:string;accessMode:string};
type ShopView = {
  configured:boolean; platform:string; shopUrl:string;
  maxProducts:number; headless:boolean; approvalFile:string;
  approvalFilePresent:boolean; profileDir:string; reportDir:string;
};
const INITIAL:PilotStatus = {
  state:"Checking…",pid:null,version:"0.2.3",configured:false,
  setupAvailable:false,running:false,lastError:null,approvalFilePresent:false,
};
const EMPTY_SHOP:ShopView={
  configured:false,platform:"amazon",shopUrl:"",maxProducts:3,headless:false,
  approvalFile:"",approvalFilePresent:false,profileDir:"",reportDir:"",
};

export function BrandingDnaPilotCard({
 nativeAvailable, controllerAvailable, managerBusy, onSelectLogs,
}:{
 nativeAvailable:boolean;controllerAvailable:boolean;managerBusy:boolean;onSelectLogs:()=>void;
}) {
 const [status,setStatus]=useState<PilotStatus>(INITIAL);
 const [shop,setShop]=useState<ShopView>(EMPTY_SHOP);
 const [library,setLibrary]=useState<SavedShop[]>([]);
 const [camShops,setCamShops]=useState<CamShop[]>([]);
 const [camLoading,setCamLoading]=useState(false);
 const [camError,setCamError]=useState("");
 const [form,setForm]=useState({platform:"amazon",shopUrl:"",maxProducts:3,headless:false});
 const [editing,setEditing]=useState(false);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [notice,setNotice]=useState("");
 const refresh=useCallback(async()=>{
   if(!nativeAvailable)return;
   const result=await invoke<PilotStatus>("branding_pilot_status");
   setStatus(result);
 },[nativeAvailable]);
 const reloadLibrary=useCallback(async()=>{
   if(!nativeAvailable)return;
   setLibrary(await invoke<SavedShop[]>("branding_pilot_list_saved_shops"));
 },[nativeAvailable]);
 const reloadCamShops=useCallback(async()=>{
   if(!nativeAvailable)return;
   setCamLoading(true);setCamError("");
   try {
     const shops=await invoke<CamShop[]>("branding_pilot_cam_shops");
     setCamShops(shops);
   }catch(e){
     setCamError(typeof e==="string"?e:e instanceof Error?e.message:"CAM Shop Library unavailable.");
   }finally{setCamLoading(false)}
 },[nativeAvailable]);
 const reloadShop=useCallback(async()=>{
   if(!nativeAvailable)return;
   const result=await invoke<ShopView>("branding_pilot_load_shop");
   setShop(result);
   setForm({platform:result.platform,shopUrl:result.shopUrl,maxProducts:result.maxProducts,headless:result.headless});
   if(!result.configured)setEditing(true);
 },[nativeAvailable]);
 useEffect(()=>{
   if(!nativeAvailable)return;
   void refresh().catch(()=>setError("Cannot read Branding DNA pilot status."));
   void reloadShop().catch(e=>setError("Cannot read shop configuration: "+(typeof e==="string"?e:e instanceof Error?e.message:"Unknown error")));
   void reloadLibrary().catch(()=>setError("Cannot read saved shop library."));
   void reloadCamShops();
   const interval=window.setInterval(()=>{
     if(!document.hidden)void refresh().catch(()=>undefined);
   },3000);
   return ()=>window.clearInterval(interval);
 },[nativeAvailable,refresh,reloadShop,reloadLibrary,reloadCamShops]);
 const disabled=!nativeAvailable||!controllerAvailable||managerBusy||busy;
 const act=async(command:"install"|"check"|"run"|"stop"|"config"|"approval")=>{
   if(command==="run" && !window.confirm(
     "Start the limited browser pilot for the configured shop? Confirm actual marketplace permission and rights review. No full-shop scan is performed."
   ))return;
   setBusy(true);setError("");setNotice("");
   try{
     if(command==="config")await invoke("branding_pilot_edit_config");
     else if(command==="approval")await invoke("branding_pilot_edit_approval");
     else await invoke("branding_pilot_action",{command});
     if(command==="approval")setNotice("Opened a default-deny approval document. Do not mark any permission as granted without real authorization.");
     await refresh();
     if(command==="approval"||command==="config")await reloadShop();
     if(command==="config")await reloadLibrary();
     if(command!=="config"&&command!=="approval")onSelectLogs();
   }catch(e){
     setError(typeof e==="string"?e:e instanceof Error?e.message:"Branding DNA action failed");
   }finally{setBusy(false)}
 };
 const save=async()=>{
   setBusy(true);setError("");setNotice("");
   try{
     const result=await invoke<ShopView>("branding_pilot_save_shop",{
       platform:form.platform,
       shopUrl:form.shopUrl.trim(),
       maxProducts:form.maxProducts,
       headless:form.headless,
     });
     setShop(result);
     setForm({platform:result.platform,shopUrl:result.shopUrl,maxProducts:result.maxProducts,headless:result.headless});
     setEditing(false);
     setNotice("Shop saved locally. Set up the authorization document before starting a browser pilot.");
     await refresh();
     await reloadLibrary();
   }catch(e){
     setError(typeof e==="string"?e:e instanceof Error?e.message:"Could not save shop config");
   }finally{setBusy(false)}
 };
 const chooseCamShop=async(id:string)=>{
   const remote=camShops.find(entry=>entry.id===id);
   if(!remote)return;
   if(!["amazon","etsy"].includes(remote.platform))return;
   setBusy(true);setError("");setNotice("");
   try{
     const result=await invoke<ShopView>("branding_pilot_save_shop",{
       platform:remote.platform,shopUrl:remote.shopUrl,
       maxProducts:Math.min(5,shop.configured?shop.maxProducts:3),
       headless:false,
     });
     setShop(result);
     setForm({platform:result.platform,shopUrl:result.shopUrl,maxProducts:result.maxProducts,headless:result.headless});
     setEditing(false);
     setNotice("Imported from CAM as a local shop. Permission is NOT automatically granted. Review its authorization before a manual pilot.");
     await reloadLibrary();
     await refresh();
   }catch(e){
     setError(typeof e==="string"?e:e instanceof Error?e.message:"Could not select CAM shop.");
   }finally{setBusy(false)}
 };
 const chooseSaved=async(id:string)=>{
   if(!id)return;
   setBusy(true);setError("");setNotice("");
   try{
     const result=await invoke<ShopView>("branding_pilot_select_shop",{id});
     setShop(result);
     setForm({platform:result.platform,shopUrl:result.shopUrl,maxProducts:result.maxProducts,headless:result.headless});
     setEditing(false);
     setNotice("Selected saved shop. Browser pilot remains stopped until manually started.");
     await reloadLibrary();
     await refresh();
   }catch(e){
     setError(typeof e==="string"?e:e instanceof Error?e.message:"Unable to switch saved shop");
   }finally{setBusy(false)}
 };
 return <section className="branding-pilot-panel" aria-label="Branding DNA Amazon Etsy browser agent">
   <div className="branding-pilot-top">
     <div className="branding-pilot-icon"><Globe2 size={24}/></div>
     <div className="branding-pilot-title">
       <h2>Branding DNA · Browser Agent</h2>
       <p>Amazon + Etsy storefronts · visual browser link clicks · dedicated profile per shop</p>
     </div>
     <span className={"status-pill "+(status.running?"success":"neutral")}><span className="status-dot"/>{status.state}</span>
   </div>
   <div className="branding-pilot-meta">
     <span>Agent v{status.version}</span><span>PID {status.pid??"—"}</span>
     <span>{status.configured?"Shop configured":"Shop config required"}</span>
     <span>{status.approvalFilePresent?"Approval document present · not independently verified":"Authorization file missing"}</span>
   </div>
   <div className="branding-library-grid">
   <div className="branding-library-bar" aria-label="CAM production Shop Library">
     <label>CAM Shop Library · {camShops.length} shops
       <select value="" disabled={disabled||status.running||camLoading||camShops.length===0}
         onChange={e=>void chooseCamShop(e.target.value)}>
         <option value="">Select shop from CAM</option>
         {camShops.map(entry=><option value={entry.id} key={entry.id}>
           {entry.platform.toUpperCase()} · {entry.displayName} · {entry.status}
         </option>)}
       </select>
     </label>
     <button className="button secondary slim" type="button" disabled={disabled||camLoading}
       onClick={()=>void reloadCamShops()}>{camLoading?"Syncing…":"Refresh CAM shops"}</button>
   </div>
   <div className="branding-library-bar" aria-label="Saved shop library">
     <label>Saved shop library · {library.length}/50
       <select value={library.find(entry=>entry.active)?.id??""}
         disabled={disabled||status.running||library.length===0}
         onChange={e=>void chooseSaved(e.target.value)}>
         <option value="">Select saved shop</option>
         {library.map(entry=><option value={entry.id} key={entry.id}>
           {entry.platform.toUpperCase()} · {entry.shopUrl}{entry.approvalFilePresent?" · approval file":""}
         </option>)}
       </select>
     </label>
     <button className="button secondary slim" type="button" disabled={disabled||status.running||library.length>=50}
       onClick={()=>{
         setForm({platform:"amazon",shopUrl:"",maxProducts:3,headless:false});
         setEditing(true);setError("");
       }}><Plus size={15}/> Add shop</button>
   </div>
   </div>
   {camError&&<p className="branding-pilot-error" role="alert"><CircleAlert size={14}/>{camError}</p>}
   {editing ? <div className="branding-shop-editor" aria-label="Configure Amazon or Etsy shop">
     <div className="branding-form-grid">
       <label>Platform
         <select value={form.platform} disabled={disabled||status.running} onChange={e=>setForm({...form,platform:e.target.value})}>
           <option value="amazon">Amazon</option><option value="etsy">Etsy</option>
         </select>
       </label>
       <label className="branding-form-shop">Shop URL
         <input type="url" autoComplete="off" spellCheck={false} value={form.shopUrl}
           disabled={disabled||status.running} onChange={e=>setForm({...form,shopUrl:e.target.value})}
           placeholder={form.platform==="etsy"?"https://www.etsy.com/shop/YourShop":"https://www.amazon.com/stores/YourBrand"}/>
       </label>
       <label>Product pages
         <select value={form.maxProducts} disabled={disabled||status.running}
           onChange={e=>setForm({...form,maxProducts:Number(e.target.value)})}>
           {[1,2,3,4,5].map(n=><option key={n} value={n}>{n}</option>)}
         </select>
       </label>
       <label className="branding-form-checkbox"><input type="checkbox" checked={!form.headless}
         disabled={disabled||status.running} onChange={e=>setForm({...form,headless:!e.target.checked})}/>
         Show Chromium browser + click product links
       </label>
     </div>
     <div className="branding-pilot-actions">
       <button className="button primary slim" type="button" disabled={disabled||status.running||!form.shopUrl.trim()}
         onClick={()=>void save()}><Save size={15}/> Save shop</button>
       {shop.configured && <button className="button secondary slim" type="button" disabled={busy}
         onClick={()=>{setEditing(false);setError("")}}>Cancel</button>}
     </div>
     <small>Saving a shop creates a local configuration only. It does not grant permission or launch a browser.</small>
   </div> : shop.configured ? <div className="branding-shop-summary">
     <div><small>Selected storefront</small><strong>{shop.platform.toUpperCase()} · {shop.shopUrl}</strong><small>Up to {shop.maxProducts} pages · {shop.headless?"Headless / direct navigation":"Visible browser / actual UI clicks"}</small></div>
     <button type="button" className="button secondary slim" disabled={disabled||status.running}
       onClick={()=>setEditing(true)}><FileCog size={15}/> Change shop</button>
   </div> : null}
   <div className="branding-pilot-actions branding-main-actions">
     <button className="button primary slim" type="button" disabled={disabled||status.running||!status.configured||!status.setupAvailable||!status.approvalFilePresent}
       onClick={()=>void act("run")}><Play size={15}/> Run pilot</button>
     <button className="button secondary slim" type="button" disabled={disabled||!status.running} onClick={()=>void act("stop")}><Pause size={15}/> Stop</button>
     <button className="button secondary slim" type="button" onClick={onSelectLogs}><FolderSearch size={15}/> Live logs</button>
     <button className="button secondary slim" type="button" disabled={disabled||status.running} onClick={()=>void act("check")}><Settings2 size={15}/> Check</button>
   </div>
   <details className="branding-settings">
     <summary>Advanced tools &amp; authorization <span>Agent v{status.version} · PID {status.pid??"—"}</span></summary>
     <p className="branding-pilot-note"><ShieldCheck size={15}/>
       Manual pilot only: global Run automation and Pinterest Gemini jobs never start this browser. Each pilot observes up to 5 public product pages and records local per-shop progress; run again manually to skip previously observed products. No CAM upload or auto-retry.
     </p>
     <small>CAM shops sync read-only using existing Scout pairing; select one to create or reuse a local profile. This does not authorize scanning.</small>
     {shop.configured && <div className="branding-shop-approval">
       <div><strong>Authorization document</strong><small>{shop.approvalFilePresent?"File exists. Actual platform permission is checked when the pilot starts.":"Missing. The document is created default-deny and must reflect actual permissions."}</small>
         <code title={shop.approvalFile}>{shop.approvalFile}</code></div>
       <button type="button" className="button secondary slim" disabled={disabled||status.running}
         onClick={()=>void act("approval")}><FileCog size={15}/> Edit approval</button>
     </div>}
     <div className="branding-pilot-actions">
       <button className="button secondary slim" type="button" disabled={disabled||status.running} onClick={()=>void act("install")}><Wrench size={15}/> Install runtime</button>
       <button className="button secondary slim" type="button" disabled={disabled||status.running}
         onClick={()=>void act("config")} title="Advanced: open the JSON config in Notepad"><FileCog size={15}/> Advanced JSON</button>
     </div>
     <small className="branding-pilot-footer">Etsy requires express written authorization; Amazon requires Agent Policy compliance. CAPTCHA/login blocks stop the pilot. No seller API, third-party scraping service or production scan dispatch.</small>
   </details>
   {(error||status.lastError)&&<p className="branding-pilot-error" role="alert"><CircleAlert size={14}/>{error||status.lastError}</p>}
   {notice&&<p className="branding-pilot-notice" role="status">{notice}</p>}
   <details className="branding-results-collapse">
     <summary>Local pilot results &amp; review <span>Open saved reports and shortlist</span></summary>
     <BrandingDnaReports nativeAvailable={nativeAvailable} shopUrl={shop.configured?shop.shopUrl:""} scanRunning={status.running}/>
   </details>
 </section>;
}
