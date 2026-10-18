import { useCallback, useEffect, useState } from "react";
import "./BrandingDnaScanReadiness.css";
import { BrandingDnaScopePreview } from "./BrandingDnaScopePreview";

type Shop = { id: string; display_name: string; platform: string };
type ScanReadiness = {
  shop_id: string;
  platform: string;
  access_mode: string;
  paired_agents_online: number;
  paired_agents_seen: number;
  listing_snapshot_count: number;
  verified_listing_count: number;
  task_counts: Record<string, number>;
  policy_review: {
    state: string;
    human_review_current: boolean;
    marketplace_authorization_verified: false;
    dispatchable: false;
    counts: Record<string, number>;
  };
  scan_enabled: boolean;
  dispatchable: boolean;
  active_scan_jobs: number;
  marketplace_authorization_verified: boolean;
  blockers: string[];
};

const blockerLabels: Record<string,string> = {
  scan_task_dispatch_not_deployed: "CAM chưa bật giao thức cấp job cho Browser Agent.",
  marketplace_authorization_not_verified: "Chưa xác minh quyền truy cập và chính sách của nền tảng.",
  platform_not_supported_by_browser_pilot: "Pilot trình duyệt chỉ hỗ trợ Amazon và Etsy.",
  shop_access_not_declared: "Shop chưa khai báo quyền owned/authorized.",
  paired_agent_not_online: "Không có agent ghép nối đang heartbeat trong 3 phút gần nhất.",
  etsy_written_permission_not_verified: "Chưa có bằng chứng Etsy cho phép quét bằng văn bản.",
  amazon_agent_policy_review_not_verified: "Chưa xác nhận điều kiện Amazon Agent Policy.",
  human_policy_review_not_submitted: "Chưa nộp hồ sơ Policy của shop vào hàng chờ kiểm tra.",
  human_policy_review_pending: "Hồ sơ Policy đang chờ một người khác đánh giá.",
  human_policy_review_revoked: "Hồ sơ Policy đã bị thu hồi.",
  human_policy_review_expired_or_changed: "Hồ sơ Policy đã hết hạn hoặc nguồn/shop thay đổi.",
  human_policy_review_rejected: "Hồ sơ Policy đã bị từ chối.",
  human_policy_review_not_provider_authorization: "Hồ sơ Policy đã được người kiểm tra, nhưng chưa xác minh quyền từ nền tảng.",
  human_policy_review_requires_admin: "Chỉ quản trị shop được xem tình trạng hồ sơ Policy.",
};
const policyStateLabels: Record<string,string> = {
  not_submitted: "Chưa nộp",
  pending_human_review: "Chờ đánh giá",
  current_human_review: "Đã kiểm tra thủ công",
  revoked: "Đã thu hồi",
  expired_or_changed: "Hết hạn / thay đổi",
  rejected: "Đã từ chối",
  restricted: "Chỉ quản trị được xem",
};
const states: { id: string; label: string }[] = [
  {id:"staged",label:"Chờ kiểm chứng"},
  {id:"pending",label:"Chờ cấp job"},
  {id:"leased",label:"Đang lease"},
  {id:"retry",label:"Chờ retry"},
  {id:"blocked",label:"Bị chặn"},
  {id:"completed",label:"Hoàn thành"},
  {id:"cancelled",label:"Đã hủy"},
];

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const res = await fetch(url, {credentials: "same-origin",cache:"no-store",signal});
  if (!res.ok) throw new Error(res.status === 403 ? "Bạn không có quyền đọc dữ liệu shop." : "Không tải được trạng thái (" + res.status + ").");
  return await res.json() as T;
}

export function BrandingDnaScanReadiness() {
  const [shops,setShops] = useState<Shop[]>([]);
  const [shopId,setShopId] = useState("");
  const [readiness,setReadiness] = useState<ScanReadiness|null>(null);
  const [error,setError] = useState("");
  const [shopsLoading,setShopsLoading] = useState(true);
  const [readinessLoading,setReadinessLoading] = useState(false);
  const loading = shopsLoading || readinessLoading;
  const [refreshKey,setRefreshKey] = useState(0);

  useEffect(()=>{
    const controller = new AbortController();
    void read<{items: Shop[]}>("/api/v1/branding-dna/shops",controller.signal)
      .then(data=>{setShops(data.items.filter(s=>s.platform==="amazon"||s.platform==="etsy"));})
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"Không tải được Shop Library.");})
      .finally(()=>{if(!controller.signal.aborted)setShopsLoading(false);});
    return ()=>controller.abort();
  },[]);
  useEffect(()=>{
    if(!shops.length){setShopId("");return;}
    setShopId(id=>shops.some(shop=>shop.id===id)?id:shops[0].id);
  },[shops]);
  const refresh=useCallback(()=>setRefreshKey(n=>n+1),[]);
  useEffect(()=>{
    if(!shopId){setReadiness(null);return;}
    const controller = new AbortController();
    setReadiness(null);setError("");setReadinessLoading(true);
    void read<ScanReadiness>("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/scan-readiness",controller.signal)
      .then(data=>{
        if(data.shop_id!==shopId||data.scan_enabled!==false||data.dispatchable!==false||
           data.active_scan_jobs!==0||data.marketplace_authorization_verified!==false||
           data.policy_review?.dispatchable!==false||
           data.policy_review?.marketplace_authorization_verified!==false)
          throw new Error("Trạng thái dispatch không khớp với cấu hình an toàn.");
        if(!controller.signal.aborted)setReadiness(data);
      })
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"Không tải được kiểm tra.");})
      .finally(()=>{if(!controller.signal.aborted)setReadinessLoading(false);});
    return ()=>controller.abort();
  },[shopId,refreshKey]);
  useEffect(()=>{
    const timer=window.setInterval(()=>{if(!document.hidden)refresh();},60000);
    return ()=>window.clearInterval(timer);
  },[refresh]);

  return <section className="bdna-scan-readiness" aria-label="Shop scan readiness">
    <div className="bdna-scan-readiness-head">
      <div><h3>Điều kiện quét theo shop</h3><p>Số liệu thật từ CAM; đã kết nối agent không đồng nghĩa được phép quét.</p></div>
      <div className="bdna-scan-readiness-actions">
        <label>Shop
          <select aria-label="Shop cần kiểm tra" value={shopId} onChange={event=>setShopId(event.target.value)}>
            {!shopId&&<option value="">Chọn shop</option>}
            {shops.map(shop=><option key={shop.id} value={shop.id}>{shop.display_name} · {shop.platform.toUpperCase()}</option>)}
          </select>
        </label>
        <button type="button" onClick={refresh} disabled={loading||!shopId}>Làm mới</button>
      </div>
    </div>
    {error&&<p role="alert" className="bdna-worker-error">{error}</p>}
    {loading&&<p role="status">Đang kiểm tra trạng thái…</p>}
    {!loading&&!shopId&&<p>Chưa có shop Amazon/Etsy trong Shop Library.</p>}
    {readiness&&<>
      <div className="bdna-scan-readiness-metrics">
        <div><strong>{readiness.paired_agents_online}</strong><small>Agent online / {readiness.paired_agents_seen} đã kết nối</small></div>
        <div><strong>{readiness.listing_snapshot_count}</strong><small>Listing snapshots</small></div>
        <div><strong>{readiness.verified_listing_count}</strong><small>Listing verified</small></div>
        <div><strong>{readiness.task_counts.staged??0}</strong><small>Job mới chỉ staged</small></div>
      </div>
      <div className="bdna-scan-policy-summary">
        <h4>Hồ sơ Policy của shop</h4>
        <p><strong>{policyStateLabels[readiness.policy_review.state]??readiness.policy_review.state}</strong>{readiness.policy_review.state!=="restricted"&&<> · {readiness.policy_review.counts.current_human_review??0} hồ sơ đã kiểm tra / {readiness.policy_review.counts.total??0} hồ sơ lưu</>}</p>
        <p>Dù hồ sơ đã được người đánh giá, quyền quét tự động của Amazon/Etsy vẫn chưa được xác minh. Không có job được phát.</p>
      </div>
      <div className="bdna-scan-readiness-details">
        <div><h4>Trạng thái job</h4>
          <div className="bdna-scan-readiness-states">{states.map(state=><span key={state.id}>{state.label}: <b>{readiness.task_counts[state.id]??0}</b></span>)}</div>
          <p>Không có job nào được phát tới worker. “Staged” chỉ là metadata đang chờ đối chiếu.</p>
        </div>
        <div><h4>Điều kiện chưa đạt · {readiness.blockers.length}</h4>
          <ul>{readiness.blockers.map(key=><li key={key}>{blockerLabels[key]??key}</li>)}</ul>
        </div>
      </div>
      {readiness.policy_review.state!=="restricted"&&<BrandingDnaScopePreview shopId={shopId}/>}
    </>}
  </section>;
}
