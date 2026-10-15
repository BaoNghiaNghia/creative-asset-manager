import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./BrandingDnaWorkers.css";

type Worker = {id:string;label:string;status:string;enabled:boolean;online:boolean;agent_version:string|null;last_heartbeat:string|null};
type Enrollment = {id:string;label:string;agent_token:string};
const endpoint="/api/v1/branding-dna/workers";

async function call<T>(url:string, init?:RequestInit):Promise<T>{
 const r=await fetch(url,{credentials:"same-origin",cache:"no-store",...init,signal:init?.signal??(!init?.method||init.method==="GET"?AbortSignal.timeout(12000):undefined)});
 const data=await r.json().catch(()=>null);
 if(!r.ok)throw new Error(typeof data?.detail==="string" ? data.detail : r.status===403 ?
  "Bạn cần quyền cấu hình để quản lý worker.":"API "+r.status);
 return data as T;
}
export function BrandingDnaWorkers({ compact = false, onOpenScanner }: { compact?: boolean; onOpenScanner?: () => void } = {}) {
 const [items,setItems]=useState<Worker[]>([]);
 const [loading,setLoading]=useState(true);
 const [label,setLabel]=useState("");
 const [secret,setSecret]=useState<Enrollment|null>(null);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [copyStatus,setCopyStatus]=useState("");
 const refresh=useCallback(async()=>{const data=await call<{items:Worker[]}>(endpoint);setItems(data.items);},[]);
 useEffect(()=>{
  let alive=true;
  void refresh().catch(e=>{if(alive)setError(e instanceof Error?e.message:"Failed to load workers");})
   .finally(()=>{if(alive)setLoading(false);});
  const tick=window.setInterval(()=>{if(!document.hidden)void refresh().catch(()=>undefined);},60000);
  return ()=>{alive=false;window.clearInterval(tick);};
 },[refresh]);
 async function enroll(event:FormEvent<HTMLFormElement>){
  event.preventDefault();setBusy(true);setError("");setSecret(null);setCopyStatus("");
  try{
   const registered=await call<Enrollment>(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({label:label.trim()})});
   setSecret(registered);setLabel("");await refresh();
  }catch(e){setError(e instanceof Error?e.message:"Enrollment failed");}
  finally{setBusy(false);}
 }
 async function disable(agent:Worker){
  if(!window.confirm('Vô hiệu hóa worker "'+agent.label+'" và thu hồi token?'))return;
  setBusy(true);setError("");
  try{await call(endpoint+"/"+encodeURIComponent(agent.id)+"/disable",{method:"POST"});await refresh();}
  catch(e){setError(e instanceof Error?e.message:"Disable failed");}
  finally{setBusy(false);}
 }
 async function copyToken(){
  if(!secret)return;
  try{await navigator.clipboard.writeText(secret.agent_token);setCopyStatus("Đã sao chép · lưu vào secret store Windows.");}
  catch{setCopyStatus("Hãy sao chép token thủ công.");}
 }
 return <section className="bdna-worker-manager" aria-label="Branding DNA dedicated worker registry">
  <div className="bdna-worker-header"><div><strong>Dedicated Windows Worker</strong>
   <small>Đăng ký agent · Heartbeat · Thu hồi token · profile độc lập Pinterest Scout</small></div>
   <div className="bdna-worker-actions-header"><span className="bdna-worker-availability"><b>{items.filter(x=>x.online).length}</b> online / {items.length} registered</span>{compact && <button type="button" onClick={onOpenScanner}>Mở Browser Scanner →</button>}</div>
  </div>
  {!compact && <form className="bdna-worker-enroll" onSubmit={enroll}>
   <label>Tên agent Windows<input required minLength={3} maxLength={90} value={label}
     onChange={e=>setLabel(e.target.value)} placeholder="Branding DNA Worker - Windows PC"/></label>
   <button disabled={busy||!label.trim()} type="submit">{busy?"Đang xử lý…":"Đăng ký worker"}</button>
  </form>}
  {error&&<p role="alert" className="bdna-worker-error">{error}</p>}
  {!compact && secret && <div className="bdna-worker-token" role="status">
   <strong>Token cho {secret.label} — chỉ hiển thị một lần</strong>
   <p>Không gửi token vào chat, GitHub hoặc log. Cấu hình trên Windows bằng secret store.</p>
   <label>Agent ID<input readOnly value={secret.id}/></label>
   <label>Agent Token<input readOnly value={secret.agent_token} autoComplete="off"/></label>
   <div className="bdna-worker-actions"><button type="button" onClick={()=>void copyToken()}>Sao chép token</button>
     <button type="button" onClick={()=>{setSecret(null);setCopyStatus("");}}>Đã lưu · Ẩn token</button></div>
   {copyStatus&&<small>{copyStatus}</small>}
  </div>}
  {!compact && <div className="bdna-worker-list">
   {loading?<span>Đang tải worker…</span>:items.length===0?<span>Chưa đăng ký worker. Scan vẫn bị khóa.</span>:
   items.map(agent=><div className="bdna-worker-entry" key={agent.id}>
    <div><strong>{agent.label}</strong><small>Agent {agent.id.slice(0,8)} · {agent.agent_version??"Chưa kết nối"} · {agent.last_heartbeat?new Date(agent.last_heartbeat).toLocaleString():"Chưa có heartbeat"}</small></div>
    <span className={agent.online?"bdna-worker-online":"bdna-worker-offline"}>{agent.enabled?(agent.online?"Online":agent.status==="unhealthy"?"Unhealthy":"Offline"):"Disabled"}</span>
    {agent.enabled&&<button type="button" disabled={busy} onClick={()=>void disable(agent)}>Disable</button>}
   </div>)}
  </div>}
  <p className="bdna-worker-caution">Đăng ký worker không tự bật Scan. Chỉ bắt đầu khi adapter được cấp quyền, lease bảo mật và evidence pipeline đã được kiểm chứng.</p>
 </section>;
}
