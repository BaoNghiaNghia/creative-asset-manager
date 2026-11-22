import { useEffect, useId, useRef, useState } from "react";
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
  const [activeAspect, setActiveAspect] = useState("9:16");
  const modalTitleId = useId();
  const tabGroupId = useId();
  const openButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLElement>(null);

  const pausePlayers = () => {
    modalRef.current?.querySelectorAll("video").forEach(player => {
      try {
        if (!player.paused) player.pause();
      } catch { /* Browser media controls are optional in test environments. */ }
    });
  };
  const closeModal = () => {
    pausePlayers();
    setShow(false);
  };
  const openModal = () => {
    setActiveAspect(files.find(video => video.aspectRatio === "9:16")?.aspectRatio
      ?? files[0]?.aspectRatio ?? "9:16");
    setShow(true);
  };
  const selectAspect = (aspect: string) => {
    if (activeAspect === aspect) return;
    pausePlayers();
    setActiveAspect(aspect);
  };

  useEffect(() => {
    if (!show) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusables = Array.from(modalRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]):not([tabindex="-1"]), a[href], video[controls]'
      )).filter(node => node.getClientRects().length > 0);
      if (focusables.length === 0) return;
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      openButtonRef.current?.focus();
    };
  }, [show]);

  return <td className="etsy-video-ugc-cell" data-ugc-state={saved ? "saved" : status}>
    {saved ? <button type="button" className="etsy-video-ugc-badge" ref={openButtonRef}
      onClick={openModal}
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
      <div className="etsy-video-ugc-backdrop" role="presentation" onClick={closeModal}>
        <section ref={modalRef} role="dialog" aria-modal="true" aria-labelledby={modalTitleId}
          className="etsy-video-ugc-modal" onClick={event => event.stopPropagation()}>
          <header className="etsy-video-ugc-modal-head">
            <div className="etsy-video-ugc-modal-identity">
              <span className="etsy-video-ugc-modal-hero-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="5" width="18" height="14" rx="2"/>
                  <path d="m10 9 5 3-5 3V9ZM7 2v3M17 2v3M7 19v3M17 19v3"/>
                </svg>
              </span>
              <div className="etsy-video-ugc-modal-titles">
                <span className="etsy-video-ugc-kicker">VIDEO LIBRARY</span>
                <h2 id={modalTitleId}>Video UGC</h2>
                <p>Listing {item.listingKey} <span aria-hidden="true">·</span> Đã lưu trên Drive</p>
              </div>
            </div>
            <div className="etsy-video-ugc-modal-head-actions">
              <span className="etsy-video-ugc-modal-count">{saved}/2 video</span>
              <button ref={closeButtonRef} type="button" className="etsy-video-ugc-close"
                aria-label="Đóng video UGC" title="Đóng (Esc)" onClick={closeModal}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M5 5 19 19M19 5 5 19"/>
                </svg>
              </button>
            </div>
          </header>
          <div className="etsy-video-ugc-modal-content">
            {files.length > 1 && <div className="etsy-video-ugc-mobile-tabs"
              role="tablist" aria-label="Chọn định dạng video">
              {files.map(video => <button type="button" key={video.aspectRatio}
                role="tab" id={tabGroupId + "-tab-" + video.aspectRatio.replace(":", "-")}
                aria-controls={tabGroupId + "-panel-" + video.aspectRatio.replace(":", "-")}
                aria-selected={activeAspect === video.aspectRatio}
                className={activeAspect === video.aspectRatio ? "is-active" : ""}
                onClick={() => selectAspect(video.aspectRatio)}>
                {video.aspectRatio === "9:16" ? "Dọc" : "Ngang"}
                <span>{video.aspectRatio}</span>
              </button>)}
            </div>}
            <div className={"etsy-video-ugc-players" + (files.length === 1 ? " is-single" : "")}>
              {files.map(video => {
                const vertical = video.aspectRatio === "9:16";
                const waitingQa = video.qaStatus === "pending_review";
                const panelId = tabGroupId + "-panel-" + video.aspectRatio.replace(":", "-");
                const tabId = tabGroupId + "-tab-" + video.aspectRatio.replace(":", "-");
                return <article key={video.aspectRatio}
                  role={files.length > 1 ? "tabpanel" : undefined}
                  id={panelId} aria-labelledby={files.length > 1 ? tabId : undefined}
                  className={"etsy-video-ugc-player " + (vertical ? "is-vertical" : "is-horizontal") +
                    (activeAspect === video.aspectRatio ? " is-active" : "")}>
                  <div className="etsy-video-ugc-player-head">
                    <div className="etsy-video-ugc-player-identity">
                      <strong>{vertical ? "Video dọc" : "Video ngang"}</strong>
                      <span className="etsy-video-ugc-ratio">{video.aspectRatio}</span>
                    </div>
                    <span className={"etsy-video-ugc-qa " + (waitingQa ? "is-pending" : "is-saved")}
                      title={waitingQa
                        ? "Video đã lưu nhưng đang chờ QA nội dung. Không đồng nghĩa đạt QA."
                        : "Video đã lưu trên Drive. Trạng thái QA không xác định từ nhãn này."}>
                      <span className="etsy-video-ugc-qa-dot" aria-hidden="true"/>
                      {waitingQa ? "Chờ QA nội dung" : "Đã lưu"}
                    </span>
                  </div>
                  <div className="etsy-video-ugc-stage">
                    <video controls playsInline preload="metadata" src={video.url}
                      aria-label={"Video " + video.aspectRatio}
                      className={vertical ? "vertical" : "horizontal"}/>
                  </div>
                  <div className="etsy-video-ugc-player-foot">
                    <span className="etsy-video-ugc-file-status">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>
                      </svg>
                      Đã lưu Drive
                    </span>
                    <a href={video.url} target="_blank" rel="noopener noreferrer"
                      aria-label={"Mở file video gốc " + video.aspectRatio + " trong tab mới"}>
                      Mở file gốc
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                        strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M8 16 16 8M9 8h7v7"/>
                      </svg>
                    </a>
                  </div>
                </article>;
              })}
            </div>
          </div>
          <footer className="etsy-video-ugc-modal-footnote">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>
            </svg>
            Trạng thái lưu Drive và đánh giá QA nội dung là hai bước riêng biệt.
          </footer>
        </section>
      </div>, document.body)}
  </td>;
}
