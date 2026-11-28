import { useEffect, useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { spyFetch, type Listing, type VideoUgcItem, type VideoUgcView } from "./api";
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
  regenerating: {label: "Chờ Muse tạo lại", tone: "waiting",
    hint: "Video đã xóa và được đưa vào hàng đợi Muse."},
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
  const [phase, setPhase] = useState<PlayerPhase>("loading");
  const isVertical = video.aspectRatio === "9:16";
  const playStartedAt = useRef<number | null>(null);

  // Only the active video mounts inside the open modal. Autoplay is muted.
  // A timed-out stream releases its loading status instead of spinning forever.
  useEffect(() => {
    if (phase !== "loading" && phase !== "buffering") return;
    const timer = window.setTimeout(() => setPhase(current =>
      current === "loading" || current === "buffering" ? "error" : current), 15000);
    return () => window.clearTimeout(timer);
  }, [phase, video.url, retryToken]);

  useEffect(() => {
    setPhase("loading");
    playStartedAt.current = performance.now();
  }, [retryToken, video.url]);

  const captureRealFrame = (event: SyntheticEvent<HTMLVideoElement>) => {
    if (poster) return;
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
    (isVertical ? "is-vertical" : "is-horizontal")} data-player-phase={phase}>
    <video key={video.url + ":" + retryToken}
      controls autoPlay muted playsInline preload="auto"
      poster={poster}
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
  </div>;
}

type CoverCandidate = { src: string; source: "frame" | "poster" | "product" };

function VideoUgcCover({ item, files, posters }: {
  item: Listing; files: VideoUgcItem[]; posters: Record<string, string>;
}) {
  const [failed, setFailed] = useState<Set<string>>(() => new Set());
  const ordered = [...files].sort((a, b) =>
    Number(b.aspectRatio === "9:16") - Number(a.aspectRatio === "9:16"));
  const candidates: CoverCandidate[] = [];
  const add = (src: string | null | undefined, source: CoverCandidate["source"]) => {
    if (src && !candidates.some(candidate => candidate.src === src)) candidates.push({src, source});
  };
  for (const file of ordered) {
    add(posters[file.aspectRatio], "frame");
    add(file.thumbnailUrl || file.posterUrl, "poster");
  }
  const listingImages = [...(item.listingImages ?? [])].sort((a, b) =>
    Number(b.garmentCode === "C1717") - Number(a.garmentCode === "C1717"));
  for (const image of listingImages) add(image.mainThumbnailUrl || image.previewThumbnailUrl, "product");
  add(item.thumbnailUrl, "product");
  const candidate = candidates.find(value => !failed.has(value.src));
  const reject = () => {
    if (candidate) setFailed(previous => new Set(previous).add(candidate.src));
  };
  return <span className="etsy-video-ugc-cover" data-cover-source={candidate?.source || "none"}>
    {candidate && <>
      {candidate.source !== "product" && <img key={"blur:" + candidate.src}
        className="etsy-video-ugc-cover-blur" src={candidate.src} alt=""
        loading="lazy" decoding="async" onError={reject}/>}
      <img key={candidate.src} className="etsy-video-ugc-cover-image"
        src={candidate.src} alt="" loading="lazy" decoding="async" onError={reject}/>
      {candidate.source === "product" && <span className="etsy-video-ugc-cover-fallback"
        title="Ảnh sản phẩm thay thế; chưa tải được khung hình video">Ảnh SP</span>}
    </>}
  </span>;
}

function versionedMedia(file: VideoUgcItem): VideoUgcItem {
  if (!file.sha256) return file;
  const version = (url?: string) => url ? url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(file.sha256!) : undefined;
  return {...file, url: version(file.url)!, posterUrl: version(file.posterUrl), thumbnailUrl: version(file.thumbnailUrl)};
}

export function VideoUGCCell({ item, canDelete = false, onRegenerated }: {
  item: Listing; canDelete?: boolean; onRegenerated?: () => void;
}) {
  const [show, setShow] = useState(false);
  const [deleted, setDeleted] = useState<Record<string, string>>({});
  const [confirmSelection, setConfirmSelection] = useState<VideoUgcItem[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const confirming = confirmSelection !== null;
  const [deleteError, setDeleteError] = useState("");
  const incoming = item.videoUgc?.videos ?? [];
  const files = incoming.filter(file => !deleted[file.aspectRatio] || deleted[file.aspectRatio] !== file.sha256).map(versionedMedia);
  const saved = files.length;
  const regenerating = [...new Set([...(item.videoUgc?.regeneratingAspectRatios ?? []),
    ...Object.keys(deleted).filter(ratio => !files.some(file => file.aspectRatio === ratio))])];
  const status = regenerating.length && !saved ? "regenerating" : item.videoUgc?.status ?? "not_queued";
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
    setDeleted({});
    setConfirmSelection(null);
    setDeleteError("");
  }, [item.listingKey]);
  const mediaIdentity = incoming.map(file => file.aspectRatio + ":" + (file.sha256 || file.url)).join("|");
  useEffect(() => { setPosters({}); }, [mediaIdentity]);

  const pausePlayer = () => {
    const media = modalRef.current?.querySelector("video");
    if (media) {
      try {
        if (!media.paused) media.pause();
      } catch { /* Media not implemented in jsdom or unsupported browser. */ }
    }
  };
  const closeModal = () => {
    if (deleting) return;
    pausePlayer();
    setConfirmSelection(null);
    setDeleteError("");
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

  const beginDelete = () => {
    if (!canDelete || !video?.sha256 || deleting) return;
    pausePlayer();
    setConfirmSelection([video]);
    setDeleteError("");
  };
  const deleteSelected = async () => {
    if (!canDelete || deleting || !confirmSelection?.length) return;
    const expected = Object.fromEntries(confirmSelection.map(file => [file.aspectRatio, file.sha256]));
    if (Object.values(expected).some(sha => !sha)) return;
    setDeleting(true);
    setDeleteError("");
    try {
      const result = await spyFetch<{ok: boolean; queued: boolean; commandId: string; deletedAspectRatios: string[]; videoUgc: VideoUgcView}>(
        "/video-ugc/" + encodeURIComponent(item.listingKey) + "/regenerate",
        {method: "POST", body: JSON.stringify(expected)});
      if (!result.ok || !result.queued || !result.commandId || !result.videoUgc ||
          result.deletedAspectRatios.length !== confirmSelection.length ||
          !confirmSelection.every(file => result.deletedAspectRatios.includes(file.aspectRatio))) {
        throw new Error("CAM chưa xác nhận đã xóa và đưa video vào hàng đợi Muse.");
      }
      setDeleted(previous => ({...previous, ...Object.fromEntries(confirmSelection.map(file => [file.aspectRatio, file.sha256!]))}));
      setPosters(previous => Object.fromEntries(Object.entries(previous).filter(([ratio]) => !result.deletedAspectRatios.includes(ratio))));
      setConfirmSelection(null);
      if (!result.videoUgc.videos.length) setShow(false);
      onRegenerated?.();
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Không thể xác nhận yêu cầu tạo lại video.");
    } finally { setDeleting(false); }
  };

  useEffect(() => {
    if (!show) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (deleting) return;
        if (confirming) { setConfirmSelection(null); setDeleteError(""); return; }
        const media = modalRef.current?.querySelector("video");
        if (media && !media.paused) {
          try { media.pause(); } catch { /* Media pause may be unavailable. */ }
        }
        setShow(false);
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusable = Array.from(modalRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled):not([tabindex="-1"]), input:not(:disabled), a[href], video[controls]'
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
  }, [show, deleting, confirming]);

  return <td className="etsy-video-ugc-cell" data-ugc-state={saved ? "saved" : status}>
    {saved ? <button type="button" className="etsy-video-ugc-badge" ref={openButtonRef}
      onClick={openModal}
      title={label + " đã lưu trên Drive · " +
        (files.some(file => file.qaStatus === "pending_review")
          ? "Chờ QA nội dung, không đồng nghĩa đã đạt QA."
          : "Mở các video đã xác minh của listing " + item.listingKey)}
      aria-label={"Xem " + label + " đã lưu trên Drive cho listing " + item.listingKey}>
      <span className="etsy-video-ugc-thumb" aria-hidden="true">
        <VideoUgcCover key={item.listingKey + ":" + files.map(file => file.url + ":" + (file.thumbnailUrl ?? "") + ":" + (file.posterUrl ?? "")).join("|")}
          item={item} files={files} posters={posters}/>
        <span className="etsy-video-ugc-thumb-play">
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="m8 5 12 7-12 7V5Z" />
          </svg>
        </span>
        <span className="etsy-video-ugc-thumb-count">{saved}</span>
      </span>
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
                <p>Listing {item.listingKey} <span aria-hidden="true">·</span> {regenerating.length ? "Muse đang chờ tạo lại" : "Đã lưu trên Drive"}</p>
              </div>
            </div>
            <div className="etsy-video-ugc-modal-head-actions">
              <span className="etsy-video-ugc-modal-count">{saved}/2 video</span>
              <button ref={closeButtonRef} type="button" className="etsy-video-ugc-close"
                aria-label="Đóng video UGC" title="Đóng (Esc)" onClick={closeModal} disabled={deleting}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M5 5 19 19M19 5 5 19"/>
                </svg>
              </button>
            </div>
          </header>
          <div className="etsy-video-ugc-modal-content">
            {confirmSelection ? <div className="etsy-video-ugc-delete-confirm" role="group" aria-label="Xác nhận xóa và tạo lại video">
              <h3>Xóa và tạo lại video</h3>
              <p>Chọn video cần xóa khỏi Google Drive. Muse sẽ tạo lại bằng workflow hiện hành.</p>
              {files.map(file => <label key={file.aspectRatio}>
                <input type="checkbox" checked={confirmSelection.some(v => v.aspectRatio === file.aspectRatio)}
                  disabled={deleting || !file.sha256} onChange={event => {
                    setDeleteError("");
                    setConfirmSelection(current => event.target.checked
                      ? [...(current ?? []), file]
                      : (current ?? []).filter(v => v.aspectRatio !== file.aspectRatio));
                  }}/>
                <span>{aspectName(file.aspectRatio)} · {file.aspectRatio}</span>
              </label>)}
              <small>Video còn lại được giữ nguyên. Nếu khác workflow, hãy chọn cả hai.</small>
              {deleteError && <p role="alert" className="etsy-video-ugc-delete-error">{deleteError}</p>}
              <div className="etsy-video-ugc-delete-actions">
                <button type="button" disabled={deleting} onClick={() => {setConfirmSelection(null); setDeleteError("");}}>Hủy</button>
                <button type="button" className="etsy-video-ugc-delete-submit" disabled={deleting || !confirmSelection.length} onClick={() => void deleteSelected()}>
                  {deleting ? "Đang xóa…" : "Xóa & đưa vào Muse (" + confirmSelection.length + ")"}
                </button>
              </div>
            </div> : <div className={"etsy-video-ugc-studio " +
              (video.aspectRatio === "9:16" ? "is-vertical" : "is-horizontal") +
              (files.length === 1 ? " is-single" : "")}>
              <div className={"etsy-video-ugc-primary " +
                (video.aspectRatio === "9:16" ? "is-vertical" : "is-horizontal")}
                id={tabGroupId + "-panel"} role={files.length > 1 ? "tabpanel" : "group"}
                aria-labelledby={files.length > 1 ? activeTabId : undefined}
                aria-label={files.length === 1 ? aspectName(video.aspectRatio) : undefined}>
                <VideoUgcActivePlayer key={video.url}
                  video={video} retryToken={retryToken}
                  poster={posters[video.aspectRatio] || video.posterUrl}
                  onPoster={frame => savePoster(video.aspectRatio, frame)}
                  onRetry={() => setRetryToken(value => value + 1)}/>
                <div className="etsy-video-ugc-primary-foot">
                  {canDelete && video.sha256 && !regenerating.length && <button type="button" className="etsy-video-ugc-delete" onClick={beginDelete} aria-label="Xóa và tạo lại video">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>
                    Xóa & tạo lại
                  </button>}
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
            </div>}
          </div>
        </section>
      </div>, document.body)}
  </td>;
}
