import { useState } from "react";
import { createPortal } from "react-dom";
import type { Listing } from "./api";
import "./VideoUGCCell.css";

const LABELS: Record<string,string> = {
  not_queued: "Chưa có", missing_source: "Thiếu nguồn",
  qa_hold: "Chờ QA ảnh", queued: "Đang chờ",
  rendering: "Đã nhận job", submitted: "Chờ kiểm tra",
  receipt_only: "Chờ lưu Drive", partial: "1/2 video",
  saved: "Đã lưu Drive", completed: "Đã lưu Drive",
};
export function VideoUGCCell({ item }: { item: Listing }) {
  const [show, setShow] = useState(false);
  const view = item.videoUgc;
  const files = view?.videos ?? [];
  const saved = files.length;
  const status = view?.status ?? "not_queued";
  const label = saved ? (saved + "/2 video") : LABELS[status] || "Chưa có";
  return <td className="etsy-video-ugc-cell">
    {saved ? <button type="button" className="etsy-video-ugc-badge"
      onClick={()=>setShow(true)} title={"Xem video đã lưu vào Drive cho listing " + item.listingKey}>
      <span aria-hidden="true">▶</span> {label}
    </button> : <span className="etsy-video-ugc-placeholder" title={
      status === "rendering" ? "Muse đã nhận job nhưng chưa gửi MP4 xác minh" :
      status === "qa_hold" ? "Artwork cần QA trước khi tạo video" :
      status === "receipt_only" ? "Có biên nhận nhưng video chưa được CAM xác minh và lưu Drive" :
      "Chưa có video đã xác minh trong Drive"
    }>{label}</span>}
    {show && saved > 0 && createPortal(
      <div className="etsy-video-ugc-backdrop" role="presentation" onClick={()=>setShow(false)}>
        <section role="dialog" aria-modal="true" aria-label={"Video UGC listing " + item.listingKey}
          className="etsy-video-ugc-modal" onClick={e=>e.stopPropagation()}>
          <div className="etsy-video-ugc-modal-head">
            <div>
              <strong>Video UGC</strong>
              <small>Listing {item.listingKey} · {saved}/2 video · Drive</small>
            </div>
            <button type="button" aria-label="Đóng video UGC" onClick={()=>setShow(false)}>×</button>
          </div>
          <div className="etsy-video-ugc-players">
            {files.map(video => <article key={video.aspectRatio} className="etsy-video-ugc-player">
              <div className="etsy-video-ugc-player-head">
                <strong>{video.aspectRatio}</strong>
                <small>{video.qaStatus === "pending_review" ? "Chờ QA nội dung" : "Đã lưu"}</small>
              </div>
              <video controls playsInline preload="metadata"
                src={video.url} aria-label={"Video " + video.aspectRatio}
                className={video.aspectRatio === "9:16" ? "vertical" : "horizontal"} />
              <a href={video.url} target="_blank" rel="noopener noreferrer">
                Mở video
              </a>
            </article>)}
          </div>
        </section>
      </div>, document.body)}
  </td>;
}
