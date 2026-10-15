import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./BrandingDnaFoundation.css";

type Shop = {
 id:string;display_name:string;platform:string;region:string;storefront_url:string;
 access_mode:string;status:string;created_at:string;
};
type Status = {phase:string;shop_count:number;registered_workers:number;scanner_ready:boolean;scan_enabled:boolean;blockers:string[]};

async function jsonRequest<T>(url:string, init?:RequestInit):Promise<T> {
 const response = await fetch(url, {credentials:"same-origin", cache:"no-store", ...init, signal: init?.signal ?? (!init?.method || init.method === "GET" ? AbortSignal.timeout(12000) : undefined)});
 const data = await response.json().catch(()=>null);
 if(!response.ok) throw new Error(typeof data?.detail==="string" ? data.detail : response.status===403 ? "Không đủ quyền cấu hình shop." : `API error ${response.status}`);
 return data as T;
}
export function BrandingDnaFoundation({ compact = false, onOpenShops }: { compact?: boolean; onOpenShops?: () => void } = {}) {
 const [status,setStatus]=useState<Status|null>(null);
 const [shops,setShops]=useState<Shop[]>([]);
 const [busy,setBusy]=useState(true);
 const [saving,setSaving]=useState(false);
 const [open,setOpen]=useState(false);
 const [error,setError]=useState("");
 const [platform,setPlatform]=useState("etsy");
 const [displayName,setDisplayName]=useState("");
 const [url,setUrl]=useState("");
 const [region,setRegion]=useState("US");
 const [accessMode,setAccessMode]=useState("public_observation");
 const refresh = useCallback(async () => {
  const [s,list]=await Promise.all([
   jsonRequest<Status>("/api/v1/branding-dna/status"),
   jsonRequest<{items:Shop[]}>("/api/v1/branding-dna/shops"),
  ]);
  setStatus(s);setShops(list.items);
 },[]);
 useEffect(()=>{
  let active=true;
  void refresh().catch(e=>{if(active)setError(e instanceof Error?e.message:"Failed to load Shops");}).finally(()=>{if(active)setBusy(false);});
  return ()=>{active=false;};
 },[refresh]);
 async function createShop(event:FormEvent<HTMLFormElement>) {
  event.preventDefault();setSaving(true);setError("");
  try {
   await jsonRequest<Shop>("/api/v1/branding-dna/shops",{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({platform,display_name:displayName.trim(),storefront_url:url.trim(),region,access_mode:accessMode})
   });
   await refresh();setOpen(false);setDisplayName("");setUrl("");
  }catch(e){setError(e instanceof Error?e.message:"Cannot save shop");}
  finally{setSaving(false);}
 }
 return <section className="bdna-live-section" aria-label="Branding DNA real shop registry">
  <div className="bdna-live-heading">
   <div><strong>Shop Registry</strong><small>Production data · tenant-scoped · no automatic scanning</small></div>
   {compact ? <button type="button" onClick={onOpenShops}>Mở Shop Library →</button> : <button type="button" onClick={()=>{setOpen(x=>!x);setError("");}}>{open?"Đóng biểu mẫu":"+ Thêm shop"}</button>}
  </div>
  <div className="bdna-live-summary" aria-label="Branding DNA live statistics">
   <article><small>SHOPS SAVED</small><strong>{busy?"…":status?.shop_count??0}</strong><span>Shop được lưu theo tenant</span></article>
   <article><small>WINDOWS WORKERS</small><strong>{busy?"…":status?.registered_workers??0}</strong><span>Agent đã đăng ký</span></article>
   <article className="bdna-live-not-ready"><small>SCANNER STATUS</small><strong>{status?.scanner_ready && status?.scan_enabled ? "Ready" : "Not connected"}</strong><span>{status?.scanner_ready && status?.scan_enabled ? "Đã bật trên server" : "Chờ worker và evidence pipeline"}</span></article>
  </div>
  <p className="bdna-live-info">
   <strong>Phase 1 · Foundation.</strong> Bạn có thể lưu shop vào database CAM. Tính năng Scan vẫn bị khóa trên server
   cho đến khi Browser Worker chuyên dụng, quyền truy cập và pipeline evidence được kiểm chứng.
  </p>
  {error&&<div className="bdna-live-error" role="alert">{error}</div>}
  {!compact && open && <form className="bdna-shop-form" onSubmit={createShop}>
   <label>Tên shop<input required minLength={2} maxLength={90} value={displayName} onChange={e=>setDisplayName(e.target.value)} placeholder="Example Hats"/></label>
   <label>Nền tảng<select value={platform} onChange={e=>setPlatform(e.target.value)}>
    <option value="etsy">Etsy</option><option value="amazon">Amazon</option><option value="shopify">Shopify</option>
   </select></label>
   <label>Region<input required value={region} maxLength={10} onChange={e=>setRegion(e.target.value)} placeholder="US"/></label>
   <label>Chế độ truy cập<select value={accessMode} onChange={e=>setAccessMode(e.target.value)}>
    <option value="public_observation">Public observation (subject to platform rules)</option>
    <option value="owned">Owned shop</option><option value="authorized">Authorized shop</option>
   </select></label>
   <label className="bdna-shop-form-url">Storefront HTTPS URL<input required inputMode="url" type="url" value={url} onChange={e=>setUrl(e.target.value)} placeholder={platform==="etsy"?"https://www.etsy.com/shop/ShopName":platform==="amazon"?"https://www.amazon.com/stores/BrandName/page/...":"https://yourshop.myshopify.com"}/></label>
   <div className="bdna-shop-form-footer"><small>Tạo shop ở trạng thái draft, không tự mở browser.</small><button type="submit" disabled={saving}>{saving?"Đang lưu…":"Lưu shop"}</button></div>
  </form>}
  {!compact && (shops.length>0?<div className="bdna-shop-cards">{shops.map(shop=><article key={shop.id} className="bdna-shop-card">
   <div><strong>{shop.display_name}</strong><small>{shop.platform.toUpperCase()} · {shop.region} · {shop.access_mode}</small></div>
   <a href={shop.storefront_url} target="_blank" rel="noopener noreferrer" title="Open storefront in new tab">Nguồn gốc ↗</a>
   <span className="bdna-shop-draft">{shop.status}</span>
  </article>)}</div>:<p className="bdna-empty">{busy?"Đang tải dữ liệu shop…":"Chưa có shop thật. Nhấn “Thêm shop” để bắt đầu."}</p>)}
  <details className="bdna-preview-explainer"><summary>Trạng thái dữ liệu và kết nối</summary>
   Shop Registry sử dụng dữ liệu thực theo tenant. Browser Scanner và pipeline evidence chưa kết nối;
   phần prototype độc lập chỉ dùng dữ liệu mô phỏng để duyệt thiết kế.
  </details>
 </section>;
}
