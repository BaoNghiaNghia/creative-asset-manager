import { useEffect, useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import type { Listing, VideoUgcItem } from "./api";
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

type PlayerPhase = "loading" | "ready" | "buffering" | "error";

function aspectName(aspect: string) {
  return aspect === "9:16" ? "Video dọc" : "Video ngang";
}

function VideoUgcActivePlayer({
  video, retryToken, poster, onPoster, onRetry,
}: {
  video: VideoUgcItem;
  retryToken: number;
  poster?: string;
  onPoster: (image: string) => void;
  onRetry: () => void;
}) {
  const [started, setStarted] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);
  const [phase, setPhase] = useState<PlayerPhase>("loading");
  const playerRef = useRef<HTMLVideoElement>(null);
  const isVertical = video.aspectRatio === "9:16";
  const playStartedAt = useRef<number | null>(null);

  // MP4 is not mounted and therefore not requested until a user clicks Play.
  // A timed-out stream releases its loading status instead of spinning forever.
  useEffect(() => {
    if (!started || (phase !== "loading" && phase !== "buffering")) return;
    const timer = window.setTimeout(() => setPhase(current =>
      current === "loading" || current === "buffering" ? "error" : current), 15000);
    return () => window.clearTimeout(timer);
  }, [started, phase, video.url, retryToken]);

  useEffect(() => {
    if (!started) return;
    setPhase("loading");
    playStartedAt.current = performance.now();
  }, [retryToken, started]);

  const captureRealFrame = (event: SyntheticEvent<HTMLVideoElement>) => {
    if (poster && !posterFailed) return;
    const element = event.currentTarget;
    if (!element.videoWidth || !element.videoHeight) return;
    try {
      const canvas = document.createElement("canvas");
      const scale = Math.min(1, 180 / Math.max(element.videoWidth, element.videoHeight));
      canvas.width = Math.max(1, Math.round(element.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(element.videoHeight * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(element, 0, 0, canvas.width, canvas.height);
      onPoster(canvas.toDataURL("image/jpeg", 0.68));
    } catch {
      // Same-origin cache may be offline or canvas may be disabled; icon fallback.
    }
  };
  const reportFirstFrame = () => {
    const start = playStartedAt.current;
    if (start === null) return;
    playStartedAt.current = null;
    try {
      const duration = Math.round(performance.now() - start);
      performance.measure("cam-ugc-first-frame", {
        start: performance.now() - duration, duration,
        detail: {aspect: video.aspectRatio},
      });
    } catch { /* Telemetry is local only and must never affect playback. */ }
  };

  return <div className={"etsy-video-ugc-player-stage " +
    (isVertical ? "is-vertical" : "is-horizontal")} data-player-phase={started ? phase : "poster"}>
    {!started ? <button type="button"
      className="etsy-video-ugc-poster-action"
      aria-label={"Phát " + aspectName(video.aspectRatio)}
      onClick={() => { playStartedAt.current = performance.now(); setStarted(true); }}>
      {poster && !posterFailed &&
        <img src={poster} alt="" decoding="async"
          onError={() => setPosterFailed(true)} />}
      <span className="etsy-video-ugc-poster-play" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="m8 5 12 7-12 7V5Z"/></svg>
      </span>
      <span className="etsy-video-ugc-poster-label">Nhấn để phát · {video.aspectRatio}</span>
    </button> : <>
      <video key={video.url + ":" + retryToken} ref={playerRef}
        controls autoPlay playsInline preload="auto"
        poster={!posterFailed ? poster : undefined}
        src={video.url}
        className={isVertical ? "vertical" : "horizontal"}
        aria-label={"Phát " + aspectName(video.aspectRatio)}
        onLoadedMetadata={() => setPhase("buffering")}
        onCanPlay={() => setPhase("ready")}
        onPlaying={() => { setPhase("ready"); reportFirstFrame(); }}
        onLoadedData={event => { setPhase("ready"); captureRealFrame(event); }}
        onWaiting={() => setPhase("buffering")}
        onStalled={() => setPhase("buffering")}
        onError={() => setPhase("error")}/>
      {phase === "loading" && <div className="etsy-video-ugc-load-status" role="status">
        <span className="etsy-video-ugc-spinner" aria-hidden="true"/>
        Đang chuẩn bị video…
      </div>}
      {phase === "buffering" && <div className="etsy-video-ugc-load-status" role="status">
        <span className="etsy-video-ugc-spinner" aria-hidden="true"/>
        Đang tải dữ liệu video…
      </div>}
      {phase === "error" && <div className="etsy-video-ugc-error" role="alert">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>
        </svg>
        <strong>Không thể tải video</strong>
        <span>Đường truyền hoặc nguồn MP4 có thể đang gián đoạn.</span>
        <button type="button" onClick={onRetry}>Thử lại</button>
      </div>}
    </>}
  </div>;
}

export function VideoUGCCell({ item }: { item: Listing }) {
  const [show, setShow] = useState(false);
  const files = item.videoUgc?.videos ?? [];
  const saved = files.length;
  const status = item.videoUgc?.status ?? "not_queued";
  const state = getVideoUgcState(status);
  const label = saved ? (saved + "/2 video") : state.label;
  const [activeAspect, setActiveAspect] = useState<VideoUgcItem["aspectRatio"]>("9:16");
  const [retryToken, setRetryToken] = useState(0);
  const [posters, setPosters] = useState<Record<string, string>>({});
  const modalTitleId = useId();
  const tabGroupId = useId();
  const openButtonRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLElement>(null);
  const video = files.find(file => file.aspectRatio === activeAspect) ?? files[0];
  const activeTabId = tabGroupId + "-tab-" + (video?.aspectRatio ?? "9:16").replace(":", "-");

  useEffect(() => {
    setShow(false);
    setPosters({});
    setRetryToken(0);
  }, [item.listingKey]);

  const pausePlayer = () => {
    const media = modalRef.current?.querySelector("video");
    if (media) {
      try {
        if (!media.paused) media.pause();
      } catch { /* Media not implemented in jsdom or unsupported browser. */ }
    }
  };
  const closeModal = () => {
    pausePlayer();
    setShow(false);
  };
  const openModal = () => {
    setActiveAspect(files.find(file => file.aspectRatio === "9:16")?.aspectRatio ??
      files[0]?.aspectRatio ?? "9:16");
    setRetryToken(0);
    setShow(true);
  };
  const selectVideo = (aspect: VideoUgcItem["aspectRatio"]) => {
    if (video?.aspectRatio === aspect) return;
    pausePlayer();
    setRetryToken(0);
    setActiveAspect(aspect);
  };
  const savePoster = (aspect: string, frame: string) =>
    setPosters(current => current[aspect] ? current : {...current, [aspect]: frame});

  useEffect(() => {
    if (!show) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        const media = modalRef.current?.querySelector("video");
        if (media && !media.paused) {
          try { media.pause(); } catch { /* Media pause may be unavailable. */ }
        }
        setShow(false);
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusable = Array.from(modalRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled):not([tabindex="-1"]), a[href], video[controls]'
      )).filter(node => node.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
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
        (files.some(file => file.qaStatus === "pending_review")
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
    {show && video && createPortal(
      <div className="etsy-video-ugc-backdrop" role="presentation" onClick={closeModal}>
        <section ref={modalRef} className={"etsy-video-ugc-modal " +
          (video.aspectRatio === "9:16" ? "is-vertical" : "is-horizontal") +
          (files.length === 1 ? " is-single" : "")}
          role="dialog" aria-modal="true" aria-labelledby={modalTitleId}
          onClick={event => event.stopPropagation()}>
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
            <div className={"etsy-video-ugc-studio " +
              (video.aspectRatio === "9:16" ? "is-vertical" : "is-horizontal") +
              (files.length === 1 ? " is-single" : "")}>
              <div className={"etsy-video-ugc-primary " +
                (video.aspectRatio === "9:16" ? "is-vertical" : "is-horizontal")}
                id={tabGroupId + "-panel"} role={files.length > 1 ? "tabpanel" : "group"}
                aria-labelledby={files.length > 1 ? activeTabId : undefined}
                aria-label={files.length === 1 ? aspectName(video.aspectRatio) : undefined}>
                <div className="etsy-video-ugc-primary-head">
                  <div className="etsy-video-ugc-player-identity">
                    <strong>{aspectName(video.aspectRatio)}</strong>
                    <span className="etsy-video-ugc-ratio">{video.aspectRatio}</span>
                  </div>
                  <span className={"etsy-video-ugc-qa " +
                    (video.qaStatus === "pending_review" ? "is-pending" : "is-saved")}
                    title={video.qaStatus === "pending_review"
                      ? "Video đã lưu trên Drive, đang chờ QA nội dung."
                      : "Video đã lưu trên Drive; không suy ra QA đạt."}>
                    <span className="etsy-video-ugc-qa-dot" aria-hidden="true"/>
                    {video.qaStatus === "pending_review" ? "Chờ QA nội dung" : "Đã lưu"}
                  </span>
                </div>
                <VideoUgcActivePlayer key={video.url}
                  video={video} retryToken={retryToken}
                  poster={posters[video.aspectRatio] || video.posterUrl}
                  onPoster={frame => savePoster(video.aspectRatio, frame)}
                  onRetry={() => setRetryToken(value => value + 1)}/>
                <div className="etsy-video-ugc-primary-foot">
                  <a href={video.url} target="_blank" rel="noopener noreferrer"
                    aria-label={"Mở file video gốc " + video.aspectRatio + " trong tab mới"}>
                    Mở file gốc
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                      strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                      <path d="m7 17 10-10M9 7h8v8"/>
                    </svg>
                  </a>
                </div>
              </div>
              <aside className="etsy-video-ugc-selector" aria-label="Chọn phiên bản video">
                {files.length > 1 && <div className="etsy-video-ugc-version-group"
                  role="tablist" aria-label="Chọn định dạng video">
                  {files.map(file => {
                    const active = video.aspectRatio === file.aspectRatio;
                    const vertical = file.aspectRatio === "9:16";
                    return <button type="button" key={file.aspectRatio}
                      id={tabGroupId + "-tab-" + file.aspectRatio.replace(":", "-")}
                      className={"etsy-video-ugc-version" + (active ? " is-active" : "")}
                      role="tab" tabIndex={active ? 0 : -1}
                      aria-selected={active} aria-controls={tabGroupId + "-panel"}
                      onClick={() => selectVideo(file.aspectRatio)}
                      onKeyDown={event => {
                        const index = files.findIndex(x => x.aspectRatio === file.aspectRatio);
                        const next = event.key === "ArrowRight" || event.key === "ArrowDown"
                          ? (index + 1) % files.length
                          : event.key === "ArrowLeft" || event.key === "ArrowUp"
                            ? (index + files.length - 1) % files.length
                            : event.key === "Home" ? 0
                              : event.key === "End" ? files.length - 1 : -1;
                        if (next >= 0) {
                          event.preventDefault();
                          selectVideo(files[next].aspectRatio);
                          const nextId = tabGroupId + "-tab-" +
                            files[next].aspectRatio.replace(":", "-");
                          document.getElementById(nextId)?.focus();
                        }
                      }}>
                      <span className={"etsy-video-ugc-version-preview " +
                        (vertical ? "is-vertical" : "is-horizontal")}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                          strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                          <rect x="5" y="3" width="14" height="18" rx="2"/>
                          <path d="m10 9 5 3-5 3V9Z"/>
                        </svg>
                        {(posters[file.aspectRatio] || file.thumbnailUrl) &&
                          <img src={posters[file.aspectRatio] || file.thumbnailUrl} alt=""
                            loading="lazy" decoding="async"
                            onError={event => { event.currentTarget.hidden = true; }} />}
                      </span>
                      <span className="etsy-video-ugc-version-copy">
                        <strong>{aspectName(file.aspectRatio)}</strong>
                        <small>{file.aspectRatio} <span aria-hidden="true">·</span> MP4</small>
                      </span>
                      <span className="etsy-video-ugc-version-chevron" aria-hidden="true">›</span>
                    </button>;
                  })}
                </div>}
              </aside>
            </div>
          </div>
        </section>
      </div>, document.body)}
  </td>;
}