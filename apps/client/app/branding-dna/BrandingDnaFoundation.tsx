import {useCallback,useEffect,useState,type FormEvent} from "react";
import "./BrandingDnaFoundation.css";

type Shop={
 id:string;display_name:string;platform:string;region:string;
 storefront_url:string;access_mode:string;status:string;created_at:string;
};
type Status={phase:string;shop_count:number;registered_workers:number;scanner_ready:boolean;scan_enabled:boolean};
type QueueMetrics={staged:number;pending:number;leased:number;retry:number;blocked:number;completed:number;cancelled:number;expired_leases:number;due_retries:number};
type ScanShop={shop_id:string;display_name:string;platform:string;status:string;staged_tasks:number;blockers:string[];queue?:QueueMetrics};
type Readiness={version:number;scan_enabled:boolean;ready_count:number;blocked_count:number;shop_count:number;staged_tasks:number;queue_totals?:QueueMetrics;items:ScanShop[]};
const BLOCKERS:Record<string,string>={
 scan_task_dispatch_not_deployed:"Dispatcher chưa triển khai",
 marketplace_authorization_not_verified:"Chưa xác minh quyền truy cập tự động theo nền tảng",
 etsy_written_permission_not_verified:"Cần quyền tự động hóa theo quy định Etsy",
 amazon_agent_policy_review_not_verified:"Cần rà soát Amazon Agent Policy",
 no_staged_scan_tasks:"Chưa có task",shop_paused:"Đã tạm dừng trong CAM",
};
async function jsonRequest<T>(url:string,init?:RequestInit):Promise<T>{
 const response=await fetch(url,{credentials:"same-origin",cache:"no-store",...init,
  signal:init?.signal??(!init?.method||init.method==="GET"?AbortSignal.timeout(12000):undefined)});
 const data=await response.json().catch(()=>null);
 if(!response.ok)throw new Error(typeof data?.detail==="string"?data.detail:
  response.status===403?"Không có quyền chỉnh sửa danh sách shop.":`API error ${response.status}`);
 return data as T;
}
/** Shop Registry does not authorize ownership or marketplace automation.
 * All saved storefronts, including competitors, remain browsable by a human.
 */
export function BrandingDnaFoundation({compact=false,onOpenShops}:{
 compact?:boolean;onOpenShops?:()=>void;
}={}){
 const [status,setStatus]=useState<Status|null>(null);
 const [shops,setShops]=useState<Shop[]>([]);
 const [readiness,setReadiness]=useState<Readiness|null>(null);
 const [loading,setLoading]=useState(true);
 const [saving,setSaving]=useState(false);
 const [updating,setUpdating]=useState<string|null>(null);
 const [open,setOpen]=useState(false);
 const [platform,setPlatform]=useState("etsy");
 const [url,setUrl]=useState("");
 const [notice,setNotice]=useState("");
 const [error,setError]=useState("");
 const refresh=useCallback(async()=>{
  const [s,list,scan]=await Promise.all([
   jsonRequest<Status>("/api/v1/branding-dna/status"),
   jsonRequest<{items:Shop[]}>("/api/v1/branding-dna/shops"),
   jsonRequest<Readiness>("/api/v1/branding-dna/shops/scan-readiness").catch(()=>null),
  ]);
  setStatus(s);setShops(list.items);
  setReadiness(scan&&scan.version===1&&scan.scan_enabled===false?scan:null);
 },[]);
 useEffect(()=>{
  let active=true;
  void refresh().catch(e=>{if(active)setError(e instanceof Error?e.message:"Không tải được Shop Library");})
   .finally(()=>{if(active)setLoading(false)});
  const timer=window.setInterval(()=>{if(active&&!document.hidden)void refresh().catch(()=>{})},60_000);
  return ()=>{active=false;window.clearInterval(timer)};
 },[refresh]);
 async function createShop(event:FormEvent<HTMLFormElement>){
  event.preventDefault();setSaving(true);setError("");setNotice("");
  try{
   await jsonRequest("/api/v1/branding-dna/shops",{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({platform,storefront_url:url.trim()})
   });
   await refresh();setOpen(false);setUrl("");setNotice("Đã thêm shop vào danh mục. Không yêu cầu chứng minh sở hữu.");
  }catch(e){setError(e instanceof Error?e.message:"Không thêm được shop")}
  finally{setSaving(false)}
 }
 async function setLifecycle(shop:Shop,next:"active"|"paused"){
  setUpdating(shop.id);setError("");setNotice("");
  try{
   await jsonRequest("/api/v1/branding-dna/shops/"+encodeURIComponent(shop.id)+"/lifecycle",{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({status:next})
   });
   await refresh();setNotice(next==="active"?
    "Shop đã kích hoạt trong danh mục. Điều này không cấp quyền scraping.":"Đã tạm dừng shop.");
  }catch(e){setError(e instanceof Error?e.message:"Không cập nhật được shop")}
  finally{setUpdating(null)}
 }
 return <section className="bdna-live-section" aria-label="Branding DNA shop registry">
  <div className="bdna-live-heading">
   <div><strong>Shop Registry</strong><small>Shop của bạn &amp; shop đối thủ · không yêu cầu CAM cấp quyền sở hữu</small></div>
   {compact?<button type="button" onClick={onOpenShops}>Mở Shop Library →</button>
    :<button type="button" onClick={()=>setOpen(x=>!x)}>{open?"Đóng":"+ Thêm shop"}</button>}
  </div>
  <div className="bdna-live-summary" aria-label="Branding DNA live statistics">
   <article><small>SHOPS SAVED</small><strong>{loading?"…":status?.shop_count??shops.length}</strong><span>Danh mục theo tenant</span></article>
   <article><small>WINDOWS WORKERS</small><strong>{loading?"…":status?.registered_workers??0}</strong><span>Agent đã đăng ký</span></article>
   <article className="bdna-live-not-ready"><small>SCAN DISPATCHER</small>
    <strong>{status?.scanner_ready&&status?.scan_enabled?"Ready":"Scan disabled"}</strong>
    <span>Tình trạng cấp job độc lập với danh mục shop</span>
   </article>
  </div>
  <p className="bdna-live-info">
   Mỗi shop là một nguồn nghiên cứu có thể mở công khai. CAM không yêu cầu khai báo sở hữu,
   xét duyệt tài liệu hoặc xác nhận quan hệ với shop để lưu, xem hay kích hoạt trong danh mục.
   Truy cập tự động vẫn phụ thuộc quy định từng nền tảng.
  </p>
  {error&&<div className="bdna-live-error" role="alert">{error}</div>}
  {notice&&<p className="bdna-live-info" role="status">{notice}</p>}
  {!compact&&open&&<form className="bdna-shop-form" onSubmit={createShop}>
   <label>Nền tảng<select value={platform} onChange={e=>setPlatform(e.target.value)}>
    <option value="etsy">Etsy</option><option value="amazon">Amazon</option>
   </select></label>
   <label className="bdna-shop-form-url">URL shop công khai
    <input required type="url" inputMode="url" value={url} onChange={e=>setUrl(e.target.value)}
     placeholder={platform==="etsy"?"https://www.etsy.com/shop/ShopName":"https://www.amazon.com/stores/BrandName"}/>
   </label>
   <div className="bdna-shop-form-footer"><small>Không cần khai báo owned/authorized.</small>
    <button type="submit" disabled={saving}>{saving?"Đang lưu…":"Lưu shop"}</button>
   </div>
  </form>}
  {!compact&&<p className="bdna-live-info" role="status">
   {readiness?
    `CAM: ${readiness.blocked_count}/${readiness.shop_count} shop chưa thể cấp scan job · ${readiness.staged_tasks} staged · ${readiness.queue_totals?.retry??0} retry · Dispatcher hiện tắt.`
    :"Chưa tải được trạng thái dispatcher; vẫn có thể xem danh mục shop."}
  </p>}
  {!compact&&(shops.length?<div className="bdna-shop-cards">{shops.map(shop=>{
   const blocked=readiness?.items.find(item=>item.shop_id===shop.id);
   return <article key={shop.id} className="bdna-shop-card">
    <div><strong>{shop.display_name}</strong>
     <small>{shop.platform.toUpperCase()} · {shop.region}</small>
    </div>
    <a href={shop.storefront_url} target="_blank" rel="noopener noreferrer">Mở shop ↗</a>
    <span className="bdna-shop-draft">{shop.status}</span>
    <button type="button" className="bdna-declaration-toggle"
     disabled={updating===shop.id}
     onClick={()=>void setLifecycle(shop,shop.status==="paused"?"active":"paused")}>
     {updating===shop.id?"Đang lưu…":shop.status==="paused"?"Tiếp tục quản lý":"Tạm dừng"}
    </button>
    {blocked&&<details className="bdna-shop-blockers">
     <summary>Trạng thái scan tự động</summary>
     <ul>{blocked.blockers.map(code=><li key={code}>{BLOCKERS[code]??code}</li>)}</ul>
     <small>{blocked.staged_tasks} staged · {blocked.queue?.pending??0} pending · Không có quyền quét nào được tự cấp</small>
    </details>}
   </article>
  })}</div>:<p className="bdna-empty">{loading?"Đang tải…":"Chưa có shop. Nhấn Thêm shop để bắt đầu."}</p>)}
 </section>;
}