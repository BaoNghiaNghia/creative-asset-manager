import {useEffect,useState} from "react";
import "./BrandingDnaBrowserPlan.css";

type BrowserPlan = {
  shop_id:string;
  platform:"amazon"|"etsy";
  storefront_url:string;
  mode:string;
  marketplace_api_used:boolean;
  external_scraping_service_used:boolean;
  scanner_enabled:boolean;
  dispatchable:boolean;
  pilot_max_product_pages:number;
  steps:string[];
  blockers:string[];
  result_state:string;
  listing_coverage:string;
};
const labels:Record<string,string>={
  open_shop_in_dedicated_chromium:"Mở storefront trong Chromium Worker riêng",
  find_visible_listing_cards:"Tìm card sản phẩm hiển thị trên shop",
  visit_bounded_product_pages:"Mở tối đa 5 trang chi tiết sản phẩm trong pilot",
  extract_visible_titles_and_image_references:"Đọc tên và ảnh hiển thị từ DOM, lưu nguồn tham chiếu",
  review_partial_unverified_evidence:"Đưa dữ liệu quan sát vào bước kiểm chứng thủ công",
  operator_approval_for_exact_shop:"Phê duyệt cụ thể shop cần quét",
  marketplace_terms_and_robots_policy_review:"Rà soát chính sách nền tảng và robots",
  dedicated_worker_browser_binary:"Cài Chromium + Playwright trên Windows Worker riêng",
  local_profile_isolation_and_browser_qa:"Khóa profile và kiểm thử Browser QA",
  evidence_rights_review:"Kiểm tra quyền sử dụng dữ liệu và hình ảnh",
  etsy_express_written_authorization:"Etsy: cần cho phép bằng văn bản",
  amazon_agent_policy_acknowledged_and_agent_user_agent:"Amazon: tuân thủ Agent Policy và nhận diện Agent trong User-Agent",
};

export function BrandingDnaBrowserPlan({shopId,platform}:{shopId:string;platform:"amazon"|"etsy"}) {
  const [plan,setPlan]=useState<BrowserPlan|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    const controller=new AbortController();
    setPlan(null);setError("");setLoading(true);
    void fetch("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/browser-plan",{
      credentials:"same-origin",cache:"no-store",signal:controller.signal,
    }).then(async response=>{
      if(!response.ok)throw new Error("Không thể kiểm tra kế hoạch Browser Agent ("+response.status+").");
      return await response.json() as BrowserPlan;
    }).then(value=>{
      if(controller.signal.aborted)return;
      if(value.shop_id!==shopId || value.platform!==platform ||
        value.mode!=="local_browser_dom_only" ||
        value.marketplace_api_used!==false ||
        value.external_scraping_service_used!==false ||
        value.scanner_enabled!==false || value.dispatchable!==false){
        throw new Error("Kế hoạch Agent có trạng thái không an toàn.");
      }
      setPlan(value);
    }).catch(reason=>{
      if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:"Không tải được kế hoạch.");
    }).finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return ()=>controller.abort();
  },[shopId,platform]);

  return <section className="bdna-browser-plan" aria-label="Amazon Etsy browser agent plan">
    <div className="bdna-browser-plan-heading">
      <div>
        <h4>Browser Agent · {platform==="etsy"?"Etsy":"Amazon"}</h4>
        <p>Đọc từ trang hiển thị bằng Chromium, không gọi marketplace API và không qua scraping service.</p>
      </div>
      <span className="bdna-browser-plan-status">Chưa bật quét</span>
    </div>
    {loading&&<p role="status">Đang kiểm tra kế hoạch Browser Agent…</p>}
    {error&&<p className="bdna-evidence-error" role="alert">{error}</p>}
    {plan&&<div className="bdna-browser-plan-columns">
      <div>
        <strong>Các bước Agent</strong>
        <ol>{plan.steps.map(step=><li key={step}>{labels[step]??step}</li>)}</ol>
        <small>Pilot tối đa {plan.pilot_max_product_pages} trang sản phẩm. Chưa có job được gửi tới Worker.</small>
      </div>
      <div>
        <strong>Điều kiện còn thiếu</strong>
        <ul>{plan.blockers.map(blocker=><li key={blocker}>{labels[blocker]??blocker}</li>)}</ul>
        <small>Dữ liệu từ trình duyệt chỉ là quan sát chưa xác minh, không suy ra sales/Best Seller.</small>
      </div>
    </div>}
  </section>;
}
