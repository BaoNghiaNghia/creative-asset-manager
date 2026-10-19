import {useEffect,useState} from "react";
import "./BrandingDnaReadiness.css";
import {BrandingDnaDraftPreview} from "./BrandingDnaDraftPreview";

type Shop = {id:string;display_name:string;platform:string};
type Source = {document_id:string;filename:string;category:string;source_note:string;
  sha256:string;citation:string|null;excerpt:string;trust:string};
type Readiness = {
 shop:Shop; source_counts:{listing_snapshots:number;verified_listing_snapshots:number;
 gallery_image_manifests:number;knowledge_documents:number;approved_knowledge_documents:number;
 pending_knowledge_documents:number};
 knowledge_sources:Source[];input_fingerprint:string;blockers:string[];notices:string[];
 dna_draft_ready:boolean;dna_generation_enabled:boolean;automatic_dna_mutation_enabled:boolean;
};
const messages:Record<string,string>={
 verified_product_evidence_missing:"Chưa có listing được kiểm chứng độc lập; import hiện là unverified.",
 listing_sample_truncated:"Số listing vượt giới hạn kiểm tra; cần audit coverage trước khi phân tích.",
 approved_shop_knowledge_missing_optional:"Chưa có RAG được duyệt (nguồn bổ sung, không bắt buộc).",
 imported_listings_are_unverified:"Metadata listing có dữ liệu nhưng chưa được xác minh.",
 brand_picks_pipeline_pending:"Brand Pick chưa được triển khai thành dữ liệu tín hiệu chính thức.",
 best_seller_signals_pipeline_pending:"Best Seller chưa có tín hiệu đã xác minh."
};
export function BrandingDnaReadiness({onOpenKnowledge,onOpenShops}:{
 onOpenKnowledge?:()=>void;onOpenShops?:()=>void;
}){
 const [shops,setShops]=useState<Shop[]>([]);
 const [shopId,setShopId]=useState("");
 const [data,setData]=useState<Readiness|null>(null);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState("");
 useEffect(()=>{
   const controller=new AbortController();
   void fetch("/api/v1/branding-dna/shops",{credentials:"same-origin",
     cache:"no-store",signal:controller.signal}).then(async response=>{
       if(!response.ok)throw new Error("Không có quyền xem Shop Library.");
       return await response.json() as {items:Shop[]};
     }).then(payload=>{
       setShops(payload.items);
       setShopId(old=>old||payload.items[0]?.id||"");
       if(!payload.items.length)setLoading(false);
     }).catch(e=>{if(!controller.signal.aborted){setError(String(e));setLoading(false)}});
   return ()=>controller.abort();
 },[]);
 useEffect(()=>{
   if(!shopId){setData(null);return;}
   const controller=new AbortController();
   setLoading(true);setError("");setData(null);
   void fetch("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/dna-inputs",{
      credentials:"same-origin",cache:"no-store",signal:controller.signal
   }).then(async response=>{
     if(response.status===403)throw new Error("Quyền quản trị Shop Knowledge (ai_provider.configure) là bắt buộc.");
     if(!response.ok)throw new Error("Không tải được dữ liệu đầu vào DNA ("+response.status+").");
     return await response.json() as Readiness;
   }).then(x=>{if(!controller.signal.aborted)setData(x)})
     .catch(e=>{if(!controller.signal.aborted)setError(String(e))})
     .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
   return ()=>controller.abort();
 },[shopId]);
 const counts=data?.source_counts;
 return <section className="bdna-readiness" aria-label="Brand DNA input readiness">
  <header className="bdna-readiness-head">
   <div>
    <strong>Brand DNA · Input Readiness</strong>
    <p>Kiểm tra mức độ sẵn sàng của dữ liệu listing và RAG đã duyệt trước khi tạo DNA.
       Chỉ đọc dữ liệu, không gọi model hay khởi tạo job.</p>
   </div>
   <label>Shop
    <select aria-label="Shop for DNA readiness" value={shopId}
      onChange={e=>setShopId(e.target.value)}>
      {!shopId&&<option value="">Chọn shop</option>}
      {shops.map(shop=><option key={shop.id} value={shop.id}>
        {shop.display_name} · {shop.platform.toUpperCase()}</option>)}
    </select>
   </label>
  </header>
  {error&&<p className="bdna-rag-error" role="alert">{error}</p>}
  {loading?<p className="bdna-readiness-empty">Đang kiểm tra dữ liệu shop…</p>:
  !shopId?<div className="bdna-readiness-empty">
    Chưa có shop. <button type="button" onClick={onOpenShops}>Mở Shop Library →</button>
  </div>:data&&<>
    <div className="bdna-readiness-status" role="status">
      <strong>{data.dna_draft_ready?"Có listing đã xác minh":"Chưa đủ dữ liệu để tạo Brand DNA"}</strong>
      <span>DNA generator: chưa bật · Tự thay đổi DNA: không · RAG chỉ là tham khảo</span>
    </div>
    <div className="bdna-readiness-stats">
      <article><small>Listing snapshots</small><strong>{counts?.listing_snapshots||0}</strong></article>
      <article><small>Verified listing</small><strong>{counts?.verified_listing_snapshots||0}</strong></article>
      <article><small>Gallery metadata</small><strong>{counts?.gallery_image_manifests||0}</strong></article>
      <article><small>RAG approved</small><strong>{counts?.approved_knowledge_documents||0}</strong></article>
      <article><small>RAG chờ duyệt</small><strong>{counts?.pending_knowledge_documents||0}</strong></article>
    </div>
    <div className="bdna-readiness-sections">
      <section>
        <h3>Safety gates & thiếu dữ liệu</h3>
        {data.blockers.map(key=><p key={key} className="bdna-readiness-blocker">● {messages[key]||key}</p>)}
        {data.notices.map(key=><p key={key} className="bdna-readiness-notice">• {messages[key]||key}</p>)}
      </section>
      <section>
        <div className="bdna-readiness-title">
          <h3>Approved RAG sources · có dẫn chứng</h3>
          <button type="button" onClick={onOpenKnowledge}>Mở Shop Knowledge →</button>
        </div>
        {!data.knowledge_sources.length?<p className="bdna-readiness-empty">Không có RAG được duyệt trong shop này.</p>:
          <div className="bdna-readiness-sources">
            {data.knowledge_sources.map(s=><article key={s.document_id}>
              <strong>{s.filename}</strong><small>{s.category} · {s.citation||"Không có trích dẫn"}</small>
              <p>{s.excerpt}</p>
            </article>)}
          </div>}
      </section>
    </div>
    <BrandingDnaDraftPreview key={shopId} shopId={shopId}/>
    <details className="bdna-readiness-fingerprint">
      <summary>Input fingerprint · truy vết dữ liệu</summary>
      <code>{data.input_fingerprint}</code>
      <p>Fingerprint cho bản dữ liệu đầu vào đã duyệt; không phải kết quả DNA được AI phân tích.</p>
    </details>
  </>}
 </section>;
}
