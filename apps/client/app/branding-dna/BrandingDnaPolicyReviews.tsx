import {useEffect,useState,type FormEvent} from "react";
import "./BrandingDnaPolicyReviews.css";

type Shop={id:string;display_name:string;platform:string};
type Doc={id:string;filename:string;category:string;status:string};
type Review={id:string;document_id:string;policy_scope:string;status:string;
  human_review_current:boolean;submitted_by:string;reviewed_by:string|null;
  expires_at:string;review_note:string;revoked_by:string|null;revocation_note:string;
  dispatchable:false;marketplace_authorization_verified:false;scan_enabled:false};

async function request<T>(url:string, init?:RequestInit):Promise<T>{
  const r=await fetch(url,{credentials:"same-origin",cache:"no-store",...init});
  const data=await r.json().catch(()=>null);
  if(!r.ok)throw new Error(typeof data?.detail==="string"?data.detail:"API "+r.status);
  return data as T;
}
const json=(body:object):RequestInit=>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
const base=(id:string)=>"/api/v1/branding-dna/shops/"+encodeURIComponent(id);

export function BrandingDnaPolicyReviews(){
 const [shops,setShops]=useState<Shop[]>([]);
 const [shopId,setShopId]=useState("");
 const [docs,setDocs]=useState<Doc[]>([]);
 const [items,setItems]=useState<Review[]>([]);
 const [documentId,setDocumentId]=useState("");
 const [expiry,setExpiry]=useState("");
 const [note,setNote]=useState("Đã kiểm tra phạm vi và nội dung tài liệu tại Shop Knowledge.");
 const [error,setError]=useState("");
 const [notice,setNotice]=useState("");
 const [busy,setBusy]=useState(false);
 const [version,setVersion]=useState(0);

 useEffect(()=>{
  const ctl=new AbortController();
  void request<{items:Shop[]}>("/api/v1/branding-dna/shops",{signal:ctl.signal})
   .then(d=>{if(!ctl.signal.aborted){
     const list=d.items.filter(s=>s.platform==="amazon"||s.platform==="etsy");
     setShops(list);setShopId(current=>list.some(x=>x.id===current)?current:list[0]?.id||"");
   }}).catch(e=>{if(!ctl.signal.aborted)setError(String(e))});
  return()=>ctl.abort();
 },[]);
 useEffect(()=>{
  if(!shopId){setItems([]);setDocs([]);return;}
  const ctl=new AbortController();
  setItems([]);setDocs([]);setDocumentId("");setError("");
  void Promise.all([
   request<{items:Review[]}>(base(shopId)+"/scan-policy-reviews",{signal:ctl.signal}),
   request<{items:Doc[]}>(base(shopId)+"/knowledge",{signal:ctl.signal}),
  ]).then(([reviews,knowledge])=>{
    if(ctl.signal.aborted)return;
    if(reviews.items.some(item=>item.dispatchable!==false||item.scan_enabled!==false||item.marketplace_authorization_verified!==false))
      throw Error("Trạng thái quyền truy cập bất thường; hệ thống vẫn cần khóa quét.");
    setItems(reviews.items);
    setDocs(knowledge.items.filter(doc=>doc.category==="policy"&&doc.status==="approved"));
  }).catch(e=>{if(!ctl.signal.aborted)setError(e instanceof Error?e.message:String(e))});
  return()=>ctl.abort();
 },[shopId,version]);

 async function submit(event:FormEvent){
  event.preventDefault();
  if(!shopId||!documentId||!expiry||busy)return;
  setBusy(true);setError("");setNotice("");
  try{
   const expires=new Date(expiry+"T23:59:59");
   if(!Number.isFinite(expires.getTime()))throw Error("Ngày hết hạn không hợp lệ.");
   await request(base(shopId)+"/scan-policy-reviews",json({document_id:documentId,expires_at:expires.toISOString()}));
   setNotice("Đã gửi hồ sơ cho người đánh giá khác. Chưa cấp quyền quét.");setDocumentId("");setVersion(n=>n+1);
  }catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{setBusy(false)}
 }
 async function decide(item:Review,action:"acknowledge"|"reject"|"revoke"){
  setBusy(true);setError("");setNotice("");
  try{
   await request(base(shopId)+"/scan-policy-reviews/"+encodeURIComponent(item.id)+"/decision",json({action,note:note.trim()}));
   setNotice("Đã ghi nhận quyết định. Browser Scanner vẫn tắt.");setVersion(n=>n+1);
  }catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{setBusy(false)}
 }
 return <section className="bdna-policy" aria-label="Shop policy access review">
  <header className="bdna-policy-head">
   <div><h3>Hồ sơ quyền truy cập shop</h3>
    <p>Tài liệu Policy từ Shop Knowledge → đánh giá độc lập → lưu lịch sử và thu hồi. Đây không phải xác nhận quyền quét tự động của nền tảng.</p></div>
   <label>Shop<select aria-label="Chọn shop kiểm tra policy" value={shopId} onChange={e=>setShopId(e.target.value)}>
    {!shopId&&<option value="">Chọn shop</option>}
    {shops.map(shop=><option value={shop.id} key={shop.id}>{shop.display_name} · {shop.platform.toUpperCase()}</option>)}
   </select></label>
  </header>
  {error&&<p role="alert" className="bdna-policy-error">{error}</p>}
  {notice&&<p role="status" className="bdna-policy-note">{notice}</p>}
  {!shopId?<p>Chưa có shop Amazon/Etsy trong Shop Library.</p>:<>
   <form onSubmit={submit} className="bdna-policy-form">
    <label>Tài liệu Policy đã duyệt
     <select value={documentId} onChange={e=>setDocumentId(e.target.value)} required>
      <option value="">Chọn tài liệu trong Shop Knowledge</option>
      {docs.map(doc=><option key={doc.id} value={doc.id}>{doc.filename}</option>)}
     </select>
    </label>
    <label>Hết hạn hồ sơ
     <input type="date" required value={expiry} onChange={e=>setExpiry(e.target.value)}/>
    </label>
    <button type="submit" disabled={busy||!documentId||!expiry}>Gửi kiểm tra</button>
   </form>
   {!docs.length&&<p className="bdna-policy-note">Chưa có tài liệu nhóm Policy đã duyệt. Upload và duyệt trong tab Shop Knowledge trước.</p>}
   <div className="bdna-policy-reviewer">
    <label>Ghi chú người đánh giá (tối thiểu 10 ký tự)
     <input maxLength={500} value={note} onChange={e=>setNote(e.target.value)}/></label>
   </div>
   <div className="bdna-policy-list">{items.map(item=><article key={item.id}>
    <div className="bdna-policy-item-head"><strong>{docs.find(d=>d.id===item.document_id)?.filename||"Tài liệu policy"}</strong>
     <small>{item.status} · {item.policy_scope}</small></div>
    <p>Hết hạn: {new Date(item.expires_at).toLocaleDateString("vi-VN")} · Người nộp: {item.submitted_by}{item.reviewed_by?" · Người đánh giá: "+item.reviewed_by:""}</p>
    <p>{item.human_review_current?"Hồ sơ đã được người kiểm tra; nền tảng chưa được xác minh.":"Chưa đạt / hết hiệu lực / đã thu hồi; không cấp job."}</p>
    {item.status==="pending_review"&&<div className="bdna-policy-actions">
     <button type="button" disabled={busy||note.trim().length<10} onClick={()=>void decide(item,"acknowledge")}>Xác nhận đã kiểm tra</button>
     <button type="button" disabled={busy||note.trim().length<10} onClick={()=>void decide(item,"reject")}>Từ chối</button>
    </div>}
    {item.status==="human_reviewed"&&<div className="bdna-policy-actions">
     <button type="button" disabled={busy||note.trim().length<10} onClick={()=>void decide(item,"revoke")}>Thu hồi hồ sơ</button>
    </div>}
   </article>)}
   {!items.length&&<p className="bdna-policy-empty">Chưa có hồ sơ cho shop này.</p>}</div>
  </>}
  <p className="bdna-policy-warning">Dispatch vẫn bị khóa: thiếu xác nhận độc lập từ marketplace, phạm vi sử dụng nội dung và bước kiểm tra an toàn của worker.</p>
 </section>;
}
