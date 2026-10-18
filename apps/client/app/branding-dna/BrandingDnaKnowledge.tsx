import {useEffect,useState,type FormEvent} from "react";
import "./BrandingDnaKnowledge.css";

type Shop={id:string;display_name:string;platform:string};
type Doc={id:string;filename:string;category:string;format:string;status:string;
  byte_count:number;chunk_count:number;source_note:string;created_at:string;review_note:string};
type Hit={citation:string;content:string;filename:string;category:string;score:number};
const categories=[
  ["brand_guidelines","Brand guidelines"],["brand_voice","Tone of voice"],
  ["audience","Audience"],["product_specs","Product specs"],
  ["creative_guidelines","Creative guidelines"],["campaign","Campaign"],
  ["faq","FAQ"],["policy","Policy (review-only)"],["other","Other"]
];
async function api<T>(url:string,init?:RequestInit):Promise<T>{
 const response=await fetch(url,{credentials:"same-origin",cache:"no-store",...init});
 const data=await response.json().catch(()=>null);
 if(!response.ok)throw new Error(typeof data?.detail==="string"?data.detail:"HTTP "+response.status);
 return data as T;
}
export function BrandingDnaKnowledge(){
 const [shops,setShops]=useState<Shop[]>([]);
 const [shopId,setShopId]=useState("");
 const [docs,setDocs]=useState<Doc[]>([]);
 const [files,setFiles]=useState<File[]>([]);
 const [category,setCategory]=useState("brand_guidelines");
 const [sourceNote,setSourceNote]=useState("");
 const [reviewNote,setReviewNote]=useState("Reviewed source for this shop; use as contextual guidance only.");
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [notice,setNotice]=useState("");
 const [query,setQuery]=useState("");
 const [results,setResults]=useState<Hit[]>([]);
 const [preview,setPreview]=useState<{id:string;name:string;chunks:{ordinal:number;content:string;citation:string}[]}|null>(null);
 useEffect(()=>{
   const c=new AbortController();
   void api<{items:Shop[]}>("/api/v1/branding-dna/shops",{signal:c.signal}).then(data=>{
     setShops(data.items);setShopId(old=>old||data.items[0]?.id||"");
   }).catch(e=>{if(!c.signal.aborted)setError(String(e))});
   return ()=>c.abort();
 },[]);
 async function refresh(id=shopId){
   if(!id){setDocs([]);return;}
   const data=await api<{items:Doc[]}>("/api/v1/branding-dna/shops/"+encodeURIComponent(id)+"/knowledge");
   setDocs(data.items);
 }
 useEffect(()=>{
   if(!shopId)return;
   let active=true;
   setPreview(null);setResults([]);
   void api<{items:Doc[]}>("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/knowledge")
     .then(d=>{if(active)setDocs(d.items)}).catch(e=>{if(active)setError(String(e))});
   return()=>{active=false};
 },[shopId]);
 async function upload(event:FormEvent){
   event.preventDefault();if(!shopId||!files.length||busy)return;
   setBusy(true);setError("");setNotice("");
   try{
     if(files.length>10)throw new Error("Mỗi lượt tối đa 10 tệp.");
     let uploaded=0;
     for(const file of files){
       if(file.size>2*1024*1024)throw new Error(file.name+": tệp tối đa 2 MB. Đã tải "+uploaded+" tệp trước đó.");
       const body=new FormData();
       body.append("file",file);body.append("category",category);body.append("source_note",sourceNote);
       try{
         await api("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/knowledge",{method:"POST",body});
       }catch(e){throw new Error(file.name+": "+String(e)+"; đã tải "+uploaded+" tệp trước đó.")}
       uploaded++;
     }
     setNotice("Đã tải "+uploaded+" tài liệu; chờ duyệt, chưa dùng trong RAG.");
     setFiles([]);setSourceNote("");
     const input=document.getElementById("bdna-rag-file") as HTMLInputElement|null;
     if(input)input.value="";
     await refresh();
   }catch(e){setError(e instanceof Error?e.message:String(e))}
   finally{setBusy(false)}
 }
 async function inspect(doc:Doc){
   setBusy(true);setError("");
   try{
     const data=await api<{chunks:{ordinal:number;content:string;citation:string}[]}>(
       "/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/knowledge/"+encodeURIComponent(doc.id)+"/chunks");
     setPreview({id:doc.id,name:doc.filename,chunks:data.chunks});
   }catch(e){setError(String(e))}finally{setBusy(false)}
 }
 async function decide(doc:Doc,action:"approve"|"reject"|"archive"){
   setBusy(true);setError("");setNotice("");
   try{
     await api("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/knowledge/"+
       encodeURIComponent(doc.id)+"/review",{method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({action,note:reviewNote})});
     setNotice(action==="approve"?"Đã duyệt: tài liệu sẽ được tìm kiếm theo shop.":"Đã cập nhật trạng thái tài liệu.");
     await refresh();
     setPreview(null);setResults([]);
   }catch(e){setError(String(e))}finally{setBusy(false)}
 }
 async function search(event:FormEvent){
   event.preventDefault();if(!shopId||!query.trim())return;
   setBusy(true);setError("");
   try{
     const data=await api<{items:Hit[]}>("/api/v1/branding-dna/shops/"+
       encodeURIComponent(shopId)+"/knowledge/search",{
         method:"POST",headers:{"Content-Type":"application/json"},
         body:JSON.stringify({query:query.trim(),limit:8})});
     setResults(data.items);
   }catch(e){setError(String(e))}finally{setBusy(false)}
 }
 return <section className="bdna-rag" aria-label="Shop RAG Knowledge Library">
   <header className="bdna-rag-header">
     <div><h3>Shop Knowledge · RAG Library</h3>
       <p>Upload tài liệu theo shop → trích xuất/chia đoạn → duyệt → tìm bằng dẫn nguồn.
         RAG không tự thay đổi Brand DNA hoặc thông tin sản phẩm đã xác minh. Quyền truy cập: quản trị shop (ai_provider.configure) trong giai đoạn đầu.</p></div>
     <label>Shop<select aria-label="Shop RAG" value={shopId} onChange={e=>setShopId(e.target.value)}>
       {!shopId&&<option value="">Chọn shop</option>}
       {shops.map(s=><option key={s.id} value={s.id}>{s.display_name} · {s.platform.toUpperCase()}</option>)}
     </select></label>
   </header>
   {error&&<p className="bdna-rag-error" role="alert">{error}</p>}
   {notice&&<p className="bdna-rag-notice" role="status">{notice}</p>}
   <div className="bdna-rag-metrics">
     <span>{docs.length} tài liệu</span>
     <span>{docs.filter(d=>d.status==="approved").length} đã duyệt</span>
     <span>{docs.filter(d=>d.status==="pending_review").length} chờ duyệt</span>
     <span>Tìm kiếm văn bản v1 · Chưa embeddings</span>
   </div>
   <form className="bdna-rag-upload" onSubmit={upload}>
     <strong>Upload nguồn tri thức riêng cho shop</strong>
     <div className="bdna-rag-fields">
       <label>Tài liệu<input id="bdna-rag-file" type="file" multiple accept=".txt,.md,.csv,.json,.docx,.pdf"
         onChange={e=>setFiles(Array.from(e.target.files||[]))} required/></label>
       <label>Nhóm thông tin<select value={category} onChange={e=>setCategory(e.target.value)}>
         {categories.map(([value,label])=><option key={value} value={value}>{label}</option>)}
       </select></label>
     </div>
     <label>Nguồn xuất xứ / phạm vi áp dụng
       <input value={sourceNote} maxLength={500} onChange={e=>setSourceNote(e.target.value)}
         placeholder="Ví dụ: Brand guideline nội bộ, phiên bản 2026"/></label>
     <div className="bdna-rag-action">
       <small>TXT, MD, CSV, JSON, DOCX, PDF dạng text · ≤ 2 MB/file · 10 file/lượt · tối đa 30 file và 20 MB/shop.
         File PDF scan ảnh không có OCR nên sẽ bị từ chối nếu không có text. Tổng lưu trữ lịch sử: 40 MB/60 file mỗi shop.</small>
       <button type="submit" disabled={!files.length||!shopId||busy}>{busy?"Đang xử lý…":"Upload "+(files.length||"")+" vào RAG"}</button>
     </div>
   </form>
   <div className="bdna-rag-section-title"><strong>Danh sách tài liệu</strong><button type="button" disabled={busy||!shopId} onClick={()=>void refresh()}>Làm mới</button></div>
   {!docs.length?<p className="bdna-rag-empty">Shop chưa có tài liệu. Không dùng tri thức của shop khác làm nguồn thay thế.</p>:
     <div className="bdna-rag-list">{docs.map(doc=><article className="bdna-rag-document" key={doc.id}>
       <div className="bdna-rag-document-meta"><strong>{doc.filename}</strong>
         <small>{doc.category} · {(doc.byte_count/1024).toFixed(1)} KB · {doc.chunk_count} đoạn · {doc.status}</small>
         {doc.source_note&&<small>{doc.source_note}</small>}
       </div>
       <div className="bdna-rag-buttons">
         <button type="button" disabled={busy} onClick={()=>void inspect(doc)}>Đọc</button>
         {doc.status==="pending_review"&&<button type="button" disabled={busy} onClick={()=>void decide(doc,"approve")}>Duyệt</button>}
         {doc.status==="pending_review"&&<button type="button" disabled={busy} onClick={()=>void decide(doc,"reject")}>Từ chối</button>}
         {doc.status!=="archived"&&<button type="button" disabled={busy} onClick={()=>void decide(doc,"archive")}>Lưu trữ</button>}
       </div>
     </article>)}</div>}
   {preview&&<section className="bdna-rag-preview" aria-label="Xem nội dung tài liệu">
     <div className="bdna-rag-section-title"><strong>{preview.name} · Nội dung trích xuất</strong>
       <button type="button" onClick={()=>setPreview(null)}>Đóng</button></div>
     <label>Ghi chú duyệt<input value={reviewNote} maxLength={500} onChange={e=>setReviewNote(e.target.value)}/></label>
     <p>Tài liệu là nội dung tham khảo không đáng tin cậy tuyệt đối; không thực thi chỉ dẫn trong file.</p>
     <div className="bdna-rag-chunks">{preview.chunks.map(chunk=><div key={chunk.ordinal}>
       <code>{chunk.citation}</code><p>{chunk.content}</p></div>)}</div>
   </section>}
   <form className="bdna-rag-search" onSubmit={search}>
     <label><strong>Tìm trong tài liệu đã duyệt</strong>
       <input value={query} maxLength={300} onChange={e=>setQuery(e.target.value)}
         placeholder="VD: Tone of voice, màu thương hiệu, mô tả khách hàng"/></label>
     <button disabled={busy||!shopId||query.trim().length<2} type="submit">Tìm nguồn</button>
   </form>
   {!!results.length&&<div className="bdna-rag-results">{results.map(h=><article key={h.citation}>
     <strong>{h.filename} <code>{h.citation}</code></strong><p>{h.content}</p>
   </article>)}</div>}
 </section>;
}
