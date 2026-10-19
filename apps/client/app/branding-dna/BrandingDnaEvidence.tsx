import { useEffect, useState } from "react";
import "./BrandingDnaEvidence.css";
import { BrandingDnaBrowserPlan } from "./BrandingDnaBrowserPlan";
import { BrandingDnaHandoffReview } from "./BrandingDnaHandoffReview";
import { BrandingDnaEvidenceImport } from "./BrandingDnaEvidenceImport";
import { BrandingDnaEvidenceAssessment } from "./BrandingDnaEvidenceAssessment";

type Shop = {id: string; display_name: string; platform: string; access_mode: string};
type Snapshot = {id: string; listing_key: string; source_revision: string; title: string;
  category: string; captured_at: string; image_count: number;
  evidence_state: string; coverage_state: string; source_kind: string};
type GalleryImage = {sequence_index: number; role: string; variant_key: string | null;
  rights_state: string; asset_sha256: string; media_available: boolean};
type ListingResponse = {items: Snapshot[]; count: number; offset: number; limit: number};

async function loadJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, {credentials: "same-origin", cache: "no-store", signal});
  if (!response.ok) throw new Error(response.status === 403 ? "Không có quyền đọc evidence." : "Không thể tải evidence (" + response.status + ").");
  return await response.json() as T;
}

export function BrandingDnaEvidence() {
  const [shops, setShops] = useState<Shop[]>([]);
  const [shopId, setShopId] = useState("");
  const [list, setList] = useState<ListingResponse | null>(null);
  const [page, setPage] = useState(0);
  const [reloadId,setReloadId]=useState(0);
  const [selected, setSelected] = useState<Snapshot | null>(null);
  const [gallery, setGallery] = useState<GalleryImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [listLoading, setListLoading] = useState(false);
  const [error, setError] = useState("");
  const [staging, setStaging] = useState(false);
  const [stageResult, setStageResult] = useState("");
  const canStage = shops.some(shop => shop.id === shopId && (shop.access_mode === "owned" || shop.access_mode === "authorized"));
  useEffect(() => {
    const controller = new AbortController();
    void loadJson<{items: Shop[]}>("/api/v1/branding-dna/shops", controller.signal)
      .then(data => {
        setShops(data.items);
        setShopId(previous => previous || data.items[0]?.id || "");
      })
      .catch(err => {if (!controller.signal.aborted) setError(String(err));})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!shopId) {setList(null); setSelected(null); return;}
    const controller = new AbortController();
    setListLoading(true);
    setList(null);
    setSelected(null);
    setGallery([]);
    setError("");
    void loadJson<ListingResponse>(
      "/api/v1/branding-dna/shops/" + encodeURIComponent(shopId) + "/listings?limit=20&offset=" + (page * 20),
      controller.signal,
    ).then(setList)
      .catch(err => {if (!controller.signal.aborted) setError(String(err));})
      .finally(() => {if (!controller.signal.aborted) setListLoading(false);});
    return () => controller.abort();
  }, [shopId, page, reloadId]);
  useEffect(() => {
    if (!shopId || !selected) return;
    const controller = new AbortController();
    setGallery([]);
    void loadJson<{images: GalleryImage[]}>(
      "/api/v1/branding-dna/shops/" + encodeURIComponent(shopId) + "/listings/" + encodeURIComponent(selected.id) + "/gallery",
      controller.signal,
    ).then(data => setGallery(data.images))
      .catch(err => {if (!controller.signal.aborted) setError(String(err));});
    return () => controller.abort();
  }, [shopId, selected]);

  async function stageSelected() {
    if (!selected || !canStage || staging) return;
    setStaging(true);
    setStageResult("");
    setError("");
    try {
      const response = await fetch("/api/v1/branding-dna/shops/" + encodeURIComponent(shopId) + "/scan-tasks", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({snapshot_id: selected.id}),
      });
      const data = await response.json().catch(() => null) as
        {item?: {status?: string}; detail?: string} | null;
      if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Không thể chuẩn bị job (" + response.status + ").");
      setStageResult(data?.item?.status === "staged"
        ? "Đã lưu vào danh sách chờ kiểm chứng. Scanner vẫn tắt, chưa chạy job."
        : "Job hiện ở trạng thái " + (data?.item?.status || "không xác định") + ", không tự khởi chạy.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể tạo job chờ.");
    } finally {
      setStaging(false);
    }
  }

  return <section className="bdna-evidence" aria-label="Product listing evidence">
    <header className="bdna-evidence-header">
      <div><h3>Product Explorer · Evidence</h3>
        <p>Amazon/Etsy ưu tiên Browser Agent. Quan sát sản phẩm cần kiểm chứng; scanner vẫn tắt trên production.</p></div>
      <label>Shop
        <select aria-label="Chọn shop" value={shopId} onChange={e => {setShopId(e.target.value); setPage(0);}}>
          {!shopId && <option value="">Chọn shop</option>}
          {shops.map(shop => <option key={shop.id} value={shop.id}>{shop.display_name} · {shop.platform.toUpperCase()}</option>)}
        </select>
      </label>
    </header>
    {error && <p className="bdna-evidence-error" role="alert">{error}</p>}
    {(shops.find(shop => shop.id === shopId)?.platform === "amazon" || shops.find(shop => shop.id === shopId)?.platform === "etsy") &&
      <BrandingDnaBrowserPlan key={shopId} shopId={shopId} platform={shops.find(shop => shop.id === shopId)?.platform as "amazon" | "etsy"}/>}
    {shopId && (shops.find(shop => shop.id === shopId)?.platform === "amazon" || shops.find(shop => shop.id === shopId)?.platform === "etsy") && <BrandingDnaHandoffReview key={shopId} shopId={shopId} platform={shops.find(shop => shop.id === shopId)!.platform} accessMode={shops.find(shop => shop.id === shopId)!.access_mode}/>}
    {shopId&&<BrandingDnaEvidenceImport key={shopId} shopId={shopId}
      accessMode={shops.find(shop=>shop.id===shopId)?.access_mode??"public_observation"}
      onImported={()=>{setPage(0);setReloadId(x=>x+1);}}/>}
    {loading ? <p className="bdna-evidence-empty" role="status">Đang tải danh sách shop…</p> :
      !shopId ? <p className="bdna-evidence-empty">Chưa có shop được đăng ký. Hãy thêm shop trong Shop Library.</p> :
      listLoading ? <p className="bdna-evidence-empty" role="status">Đang tải bằng chứng…</p> :
      !list?.items.length ? <p className="bdna-evidence-empty">Chưa có evidence từ Browser Agent. Xem kế hoạch quét ở trên; hệ thống không tạo dữ liệu mô phỏng và không tự quét khi chưa đủ điều kiện.</p> :
      <>
        <div className="bdna-evidence-count">{list.count} snapshot đã nhập · Tình trạng quét toàn shop: chưa xác định</div>
        <div className="bdna-evidence-list">
          {list.items.map(item => <button key={item.id} type="button"
            className={selected?.id === item.id ? "bdna-evidence-row selected" : "bdna-evidence-row"}
            aria-pressed={selected?.id === item.id} onClick={() => {setSelected(item); setStageResult("");}}>
            <strong>{item.title}</strong>
            <span>{item.listing_key} · {item.category}</span>
            <span>{item.image_count} ảnh · revision {item.source_revision}</span>
            <small>Import chưa xác minh · {new Date(item.captured_at).toLocaleDateString("vi-VN")}</small>
          </button>)}
        </div>
        <div className="bdna-evidence-pager">
          <span>Hiển thị {page * 20 + 1}–{Math.min((page + 1) * 20, list.count)} / {list.count}</span>
          <button type="button" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Trước</button>
          <button type="button" disabled={(page + 1) * 20 >= list.count} onClick={() => setPage(p => p + 1)}>Tiếp →</button>
        </div>
      </>}
    {selected && <section className="bdna-evidence-gallery" aria-label="Listing gallery evidence">
      <h4>Gallery · {selected.title}</h4>
      <p>Metadata-only: chưa lưu ảnh, không thể dùng trực tiếp để suy luận Brand DNA hoặc tạo output.</p>
      {canStage ? <div className="bdna-evidence-stage-control">
        <button type="button" onClick={() => {void stageSelected();}} disabled={staging}>
          {staging ? "Đang lưu…" : "Chuẩn bị kiểm chứng · Không quét"}
        </button>
        <small>Chỉ tạo staged job. Không phát lệnh tới Browser Worker.</small>
      </div> : <p>Chỉ shop owned/authorized mới được chuẩn bị job kiểm chứng.</p>}
      {stageResult && <p className="bdna-evidence-stage-result" role="status">{stageResult}</p>}
      <BrandingDnaEvidenceAssessment key={selected.id} shopId={shopId} snapshotId={selected.id}/>
      <div className="bdna-evidence-gallery-grid">
        {gallery.map(image => <div key={image.sequence_index} className="bdna-evidence-image">
          <strong>Ảnh {image.sequence_index + 1}</strong>
          <span>{image.role} {image.variant_key ? "· " + image.variant_key : ""}</span>
          <small>Quyền: {image.rights_state}</small>
          <code>SHA256: {image.asset_sha256.slice(0, 12)}…</code>
        </div>)}
      </div>
    </section>}
  </section>;
}
