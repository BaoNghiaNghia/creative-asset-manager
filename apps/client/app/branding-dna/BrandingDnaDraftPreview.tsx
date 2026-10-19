import {useState} from "react";
import "./BrandingDnaDraftPreview.css";
import {BrandingDnaDraftVersions} from "./BrandingDnaDraftVersions";

type Draft = {
  shop:{id:string;display_name:string;platform:string};
  input_fingerprint:string;status:"blocked"|"evidence_outline";
  preview_only:true;persisted:false;model_invoked:false;
  dna_generation_enabled:false;visual_analysis_enabled:false;image_rights_verified:false;
  best_seller_verified:false;brand_pick_verified:false;
  verified_listing_count:number;examples_truncated:boolean;categories_truncated:boolean;
  observed_categories:{category:string;listing_count:number}[];
  verified_product_examples:{
    snapshot_id:string;source_revision:string;title:string;category:string;
    citation:string;source_state:string;
  }[];
  context_references:{
    document_id:string;filename:string;category:string;citation:string|null;trust:string;
  }[];
  unresolved_sections:string[];blockers:string[];notices:string[];trust_rule:string;
};
const labels:Record<string,string>={
  visual_style_and_image_usage_rights:"Phong cách hình ảnh và quyền sử dụng ảnh",
  brand_voice_requires_human_synthesis:"Giọng thương hiệu cần tổng hợp và duyệt thủ công",
  target_audience:"Đối tượng khách hàng chưa có bằng chứng",
  brand_best_seller_claims:"Không suy đoán best seller",
  brand_pick_claims:"Không suy đoán Brand Pick",
  verified_product_evidence_missing:"Chưa có listing metadata được xác minh độc lập",
  listing_sample_truncated:"Danh sách evidence quá lớn, chưa kiểm tra toàn bộ",
  draft_example_sample_truncated:"Chỉ hiển thị tối đa 12 listing minh họa",
  draft_category_list_truncated:"Danh sách nhóm sản phẩm đã được giới hạn",
};
export function BrandingDnaDraftPreview({shopId}: {shopId:string}){
  const [draft,setDraft]=useState<Draft|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  async function loadDraft(){
    if(loading)return;
    setLoading(true);setError("");setDraft(null);
    try{
      const response=await fetch(
        "/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/dna-draft/preview",{
          credentials:"same-origin",cache:"no-store",
        }
      );
      if(!response.ok)throw new Error("Không đọc được DNA Draft ("+response.status+").");
      const result=await response.json() as Draft;
      if(result.shop?.id!==shopId || result.preview_only!==true ||
         result.persisted!==false || result.model_invoked!==false ||
         result.dna_generation_enabled!==false ||
         result.visual_analysis_enabled!==false){
        throw new Error("Phản hồi không đáp ứng hợp đồng read-only.");
      }
      setDraft(result);
    }catch(e){setError(e instanceof Error?e.message:"Không thể tạo bản xem trước.");}
    finally{setLoading(false);}
  }

  return <section className="bdna-draft" aria-label="Brand DNA evidence-backed draft preview">
    <div className="bdna-draft-head">
      <div>
        <strong>DNA Draft · Bản nháp có dẫn chứng</strong>
        <p>Chỉ tổng hợp dữ kiện listing đã xác minh và liệt kê RAG tham khảo.
          Không gọi AI, không lưu bản DNA, không sử dụng ảnh.</p>
      </div>
      <button type="button" onClick={()=>{void loadDraft();}} disabled={loading}>
        {loading?"Đang kiểm tra…":"Tạo bản xem trước (read-only)"}
      </button>
    </div>
    {error&&<p role="alert" className="bdna-draft-error">{error}</p>}
    <BrandingDnaDraftVersions shopId={shopId}
      previewFingerprint={draft?.input_fingerprint}
      previewReady={draft?.status==="evidence_outline" && draft.blockers.length===0}/>
    {draft&&<div className="bdna-draft-result" role="status">
      <div className="bdna-draft-summary">
        <strong>{draft.status==="evidence_outline"
          ?"Đề cương dữ kiện · Chưa phải Brand DNA được duyệt"
          :"Chưa đủ dữ kiện để tạo bản nháp"}</strong>
        <span>{draft.verified_listing_count} listing đã xác minh · Không lưu, không chạy model</span>
      </div>
      {draft.blockers.length>0&&<div className="bdna-draft-blockers">
        {draft.blockers.map(code=><p key={code}>• {labels[code]??code}</p>)}
      </div>}
      <div className="bdna-draft-grid">
        <section>
          <h4>Danh mục có dữ kiện</h4>
          {draft.observed_categories.length
            ? <ul>{draft.observed_categories.map(x=><li key={x.category}>
                <strong>{x.category}</strong> · {x.listing_count} listing
              </li>)}</ul>
            : <p>Chưa có danh mục được xác minh.</p>}
          <h4>Listing có dẫn chứng</h4>
          {draft.verified_product_examples.length
            ? <ul>{draft.verified_product_examples.map(x=><li key={x.snapshot_id}>
                <strong>{x.title}</strong> · {x.category}
                <code>{x.citation}</code>
              </li>)}</ul>
            : <p>Không dùng metadata chưa xác minh để tạo dữ kiện.</p>}
        </section>
        <section>
          <h4>RAG đã duyệt · chỉ tham khảo</h4>
          {draft.context_references.length
            ? <ul>{draft.context_references.map(x=><li key={x.document_id}>
                <strong>{x.filename}</strong> · {x.category}
                <code>{x.citation??"Chưa có citation"}</code>
              </li>)}</ul>
            : <p>Chưa có tài liệu RAG được duyệt.</p>}
          <h4>Những kết luận chưa được phép suy đoán</h4>
          <ul>{draft.unresolved_sections.map(x=><li key={x}>{labels[x]??x}</li>)}</ul>
        </section>
      </div>
      <details><summary>Truy xuất fingerprint và quy tắc tin cậy</summary>
        <code>{draft.input_fingerprint}</code>
        <p>{draft.trust_rule}</p>
      </details>
    </div>}
  </section>;
}
