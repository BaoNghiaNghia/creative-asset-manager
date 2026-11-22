import { useState } from "react";
import { createPortal } from "react-dom";
import type { Listing } from "./api";
import "./VideoUGCCell.css";

type VideoState = {
  label: string;
  tone: "idle" | "waiting" | "processing" | "attention";
  hint: string;
};
const VIDEO_STATES: Record<string, VideoState> = {
  not_queued: {label: "Chưa tạo", tone: "idle",
    hint: "Chưa có video được CAM xác minh và lưu trên Google Drive."},
  missing_source: {label: "Thiếu nguồn", tone: "attention",
    hint: "Chưa có nguồn hợp lệ để bắt đầu video UGC."},
  qa_hold: {label: "Chờ QA ảnh", tone: "waiting",
    hint: "Artwork cần vượt qua bước QA ảnh trước khi tạo video."},
  queued: {label: "Đang chờ", tone: "waiting",
    hint: "Job đã vào hàng đợi; chưa có video được CAM xác minh."},
  rendering: {label: "Đã nhận job", tone: "processing",
    hint: "Đã giao job cho Muse, chưa có bằng chứng MP4 được lưu."},
  claimed_unverified: {label: "Muse đã nhận", tone: "processing",
    hint: "Muse đã nhận job; chưa xác minh được tiến độ render hoặc MP4."},
  stalled: {label: "Job gián đoạn", tone: "attention",
    hint: "Lease job đã hết hạn; hệ thống cần nhận lại job."},
  submitted: {label: "Chờ xác minh", tone: "waiting",
    hint: "Đã gửi kết quả nhưng CAM chưa xác minh video lưu trên Drive."},
  receipt_only: {label: "Chờ lưu Drive", tone: "waiting",
    hint: "Đã có biên nhận, nhưng MP4 chưa được CAM xác minh và lưu."},
  partial: {label: "Chờ đồng bộ", tone: "waiting",
    hint: "Chưa có MP4 được xác minh trong danh sách video hiện tại."},
  saved: {label: "Chờ đồng bộ", tone: "waiting",
    hint: "Trạng thái báo đã lưu nhưng chưa có URL video được CAM xác minh."},
  completed: {label: "Chờ đồng bộ", tone: "waiting",
    hint: "Trạng thái báo hoàn tất nhưng chưa có URL video được CAM xác minh."},
};
export function getVideoUgcState(status: string): VideoState {
  return VIDEO_STATES[status] || VIDEO_STATES.not_queued;
}
export function VideoUGCCell({ item }: { item: Listing }) {
  const [show, setShow] = useState(false);
  const view = item.videoUgc;
  const files = view?.videos ?? [];
  const saved = files.length;
  const status = view?.status ?? "not_queued";
  const state = getVideoUgcState(status);
  const label = saved ? (saved + "/2 video") : state.label;
  return <td className="etsy-video-ugc-cell" data-ugc-state={saved ? "saved" : status}>
    {saved ? <button type="button" className="etsy-video-ugc-badge"
      onClick={()=>setShow(true)}
      title={label + " đã lưu trên Drive · " +
        (files.some(video => video.qaStatus === "pending_review")
          ? "Chờ QA nội dung, không đồng nghĩa đã đạt QA."
          : "Mở các video đã xác minh của listing " + item.listingKey)}
      aria-label={"Xem " + label + " đã lưu trên Drive cho listing " + item.listingKey}>
      <span className="etsy-video-ugc-status-icon" aria-hidden="true">▶</span>
      <span>{label}</span>
    </button> : <span className={"etsy-video-ugc-placeholder tone-" + state.tone}
      title={state.hint} aria-label={state.label + ". " + state.hint}>
      <span className="etsy-video-ugc-status-icon" aria-hidden="true">
        {state.tone === "attention" ? "!" : state.tone === "waiting" ? "◷" :
          state.tone === "processing" ? "◌" : "–"}
      </span>
      <span>{label}</span>
    </span>}
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
