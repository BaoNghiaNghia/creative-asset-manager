import { useEffect, useMemo, useState } from "react";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { RrugcSmartSearchInput } from "./RrugcSmartSearchInput";
import type {
  Stage3AnalysisStatus,
  Stage3ReviewGroup,
  Stage3ReviewGroupList,
  Stage3ReviewImage,
} from "./types";
import "./Stage3ReviewGroups.css";

type Props = {
  data: Stage3ReviewGroupList;
  loading: boolean;
  analyzing: boolean;
  message: string;
  query?: string;
  onQueryChange?: (query: string) => void;
  onAnalyze: (folderId?: string) => void;
};

export type Stage3ReviewModalEntry = {
  image: Stage3ReviewImage;
  folderName: string;
  folderPath: string;
};

type Stage3ReviewModalProps = {
  entries: Stage3ReviewModalEntry[];
  index: number;
  onIndexChange: (index: number) => void;
  onClose: () => void;
};

function completedLabel(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

function percent(value: number | null): string {
  return value == null ? "—" : Math.round(value * 100) + "%";
}

function statusLabel(status: Stage3AnalysisStatus): string {
  if (status === "queued") return "Queued";
  if (status === "analyzing") return "Analyzing";
  if (status === "ready") return "Ready";
  if (status === "rejected") return "Rejected";
  if (status === "error") return "Error";
  return "Pending";
}

function groupStatusLabel(group: Stage3ReviewGroup): string {
  if (group.status === "analyzing") return "Analyzing";
  if (group.status === "ready") return "Ready for review";
  if (group.status === "partial") return "Partially ready";
  if (group.status === "rejected") return "Rejected";
  if (group.status === "error") return "Needs attention";
  return "Pending analysis";
}

function imageDetail(image: Stage3ReviewImage): string {
  if (image.analysis_status === "rejected" && image.reject_reasons.length) {
    return image.reject_reasons.join(" · ").replaceAll("_", " ");
  }
  if (image.analysis_status === "error") {
    return image.last_error_code?.replaceAll("_", " ") || "Analysis failed";
  }
  return image.summary || (
    image.output_width && image.output_height
      ? image.output_width + "×" + image.output_height
      : "Generated image"
  );
}

function hasReview(image: Stage3ReviewImage): boolean {
  return Boolean(
    image.analysis_status === "ready"
    && image.review_text
    && image.reviewer_name
    && image.star_rating,
  );
}

export function Stage3ReviewModal({
  entries,
  index,
  onIndexChange,
  onClose,
}: Stage3ReviewModalProps) {
  const entry = entries[index];
  const count = entries.length;
  const [loadedOriginal, setLoadedOriginal] = useState<{
    stage2JobId: string;
    url: string;
  } | null>(null);
  const currentImage = entry?.image;
  const currentJobId = currentImage?.stage2_job_id || "";
  const currentPreviewUrl = currentImage?.preview_url || "";
  const currentOriginalUrl = currentImage?.original_url || "";
  const displayedImageUrl = (
    loadedOriginal?.stage2JobId === currentJobId
      ? loadedOriginal.url
      : currentPreviewUrl
  );

  useEffect(() => {
    if (!entry) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (count > 1 && event.key === "ArrowLeft") {
        event.preventDefault();
        onIndexChange((index - 1 + count) % count);
      }
      if (count > 1 && event.key === "ArrowRight") {
        event.preventDefault();
        onIndexChange((index + 1) % count);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [count, entry, index, onClose, onIndexChange]);

  useEffect(() => {
    setLoadedOriginal(null);
    if (
      !currentJobId
      || !currentOriginalUrl
      || currentOriginalUrl === currentPreviewUrl
    ) {
      return undefined;
    }

    let cancelled = false;
    const preload = new window.Image();
    preload.decoding = "async";
    preload.onload = () => {
      const decoded = typeof preload.decode === "function"
        ? preload.decode()
        : Promise.resolve();
      void decoded
        .catch(() => undefined)
        .then(() => {
          if (cancelled) return;
          setLoadedOriginal({
            stage2JobId: currentJobId,
            url: currentOriginalUrl,
          });
        });
    };
    preload.onerror = () => {
      // Keep the already-visible preview when the original cannot be loaded.
    };
    preload.src = currentOriginalUrl;

    return () => {
      cancelled = true;
      preload.onload = null;
      preload.onerror = null;
    };
  }, [currentJobId, currentOriginalUrl, currentPreviewUrl]);

  if (!entry) return null;

  const { image, folderName, folderPath } = entry;
  const starRating = image.star_rating || 0;
  const move = (direction: -1 | 1) => {
    if (count <= 1) return;
    onIndexChange((index + direction + count) % count);
  };

  return <div
    className="rrugc-stage3-review-modal-backdrop"
    role="presentation"
    onMouseDown={event => {
      if (event.currentTarget === event.target) onClose();
    }}
  >
    <section
      className="rrugc-stage3-review-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rrugc-stage3-review-modal-title"
      onMouseDown={event => event.stopPropagation()}
    >
      <button
        type="button"
        className="rrugc-stage3-review-modal-close"
        aria-label="Close review details"
        onClick={onClose}
      >×</button>

      <div className="rrugc-stage3-review-modal-media">
        <img
          src={displayedImageUrl}
          data-original-src={image.original_url}
          alt={image.source_name ? "Review image for " + image.source_name : "UGC review image"}
          decoding="async"
        />

        <button
          type="button"
          className="rrugc-stage3-review-modal-nav is-prev"
          aria-label="Previous review"
          onClick={() => move(-1)}
          disabled={count <= 1}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M15 5l-7 7 7 7" />
          </svg>
        </button>

        <button
          type="button"
          className="rrugc-stage3-review-modal-nav is-next"
          aria-label="Next review"
          onClick={() => move(1)}
          disabled={count <= 1}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </button>

        <span className="rrugc-stage3-review-modal-count">{index + 1} / {count}</span>
      </div>

      <div className="rrugc-stage3-review-modal-copy">
        <div className="rrugc-stage3-review-modal-meta">
          <span>Synthetic UGC review</span>
          <small>{folderName}{folderPath ? " · " + folderPath : ""}</small>
        </div>

        <div className="rrugc-stage3-review-modal-title-row">
          <div>
            <h3 id="rrugc-stage3-review-modal-title">{image.reviewer_name}</h3>
            <span
              className="rrugc-stage3-stars is-modal"
              aria-label={starRating + " out of 5 stars"}
            >
              {"★".repeat(starRating)}{"☆".repeat(5 - starRating)}
            </span>
          </div>
          <small>{image.scene_type || image.framing_type || "Lifestyle review"}</small>
        </div>

        <blockquote>{image.review_text}</blockquote>

        <div className="rrugc-stage3-review-modal-scores" aria-label="Review image scores">
          <span><small>UGC</small><strong>{percent(image.mobile_ugc_score)}</strong></span>
          <span><small>Photo</small><strong>{percent(image.photorealism_score)}</strong></span>
          <span><small>Product</small><strong>{percent(image.product_visibility_score)}</strong></span>
          <span><small>Review</small><strong>{percent(image.review_fit_score)}</strong></span>
        </div>

        <div className="rrugc-stage3-review-modal-source">
          <strong>{image.source_name || "Generated output"}</strong>
          {image.output_width && image.output_height && (
            <span>{image.output_width}×{image.output_height}</span>
          )}
        </div>
      </div>

    </section>
  </div>;
}

export function Stage3ReviewGroups({
  data,
  loading,
  analyzing,
  message,
  query = "",
  onQueryChange = () => undefined,
  onAnalyze,
}: Props) {
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null);
  const finished = data.ready_images + data.rejected_images;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleGroups = useMemo(() => {
    if (!normalizedQuery) return data.items;
    return data.items.filter(group => {
      const searchable = [
        group.folder_name,
        group.folder_path,
        group.status,
        ...group.images.flatMap(image => [
          image.source_name || "",
          image.reviewer_name || "",
          image.review_text || "",
          image.scene_type || "",
          image.framing_type || "",
          image.analysis_status || "",
        ]),
      ].join(" ").toLocaleLowerCase();
      return searchable.includes(normalizedQuery);
    });
  }, [data.items, normalizedQuery]);
  const searchSuggestions = useMemo(() => data.items.flatMap(group => [
    { value: group.folder_name, meta: group.folder_path || "Review folder", badge: "Folder" },
    ...group.images.slice(0, 4).flatMap(image => [
      ...(image.source_name ? [{ value: image.source_name, meta: group.folder_name, badge: "Image" }] : []),
      ...(image.reviewer_name ? [{ value: image.reviewer_name, meta: image.source_name || group.folder_name, badge: "Reviewer" }] : []),
      ...(image.scene_type ? [{ value: image.scene_type, meta: group.folder_name, badge: "Scene" }] : []),
    ]),
  ]), [data.items]);
  const reviewEntries: Stage3ReviewModalEntry[] = visibleGroups.flatMap(group => (
    group.images
      .filter(hasReview)
      .map(image => ({
        image,
        folderName: group.folder_name,
        folderPath: group.folder_path,
      }))
  ));
  const selectedReviewIndex = selectedReviewId
    ? reviewEntries.findIndex(entry => entry.image.stage2_job_id === selectedReviewId)
    : -1;

  return <section className="rrugc-stage3">
    <RrugcStageHeader
      className="rrugc-stage4-heading"
      kicker="STAGE 5 · FOLDER GROUPS → UGC REVIEW"
      title="UGC Review"
      description="Stage 4 outputs stay grouped by destination folder. Each image is analyzed once for visible person/product, UGC feel, photorealism and review-card suitability."
      actions={<div className="rrugc-stage3-header-actions">
        <div className="rrugc-stage3-stats" aria-label="Stage 5 summary">
          <span><strong>{data.total_groups}</strong><small>Folders</small></span>
          <span><strong>{data.total_images}</strong><small>Images</small></span>
          <span><strong>{finished}/{data.total_images}</strong><small>Analyzed</small></span>
        </div>
        <button
          type="button"
          className="rrugc-stage3-analyze-all"
          onClick={() => onAnalyze()}
          disabled={analyzing || data.total_images === 0}
        >
          {analyzing ? "Queueing…" : "Analyze all"}
        </button>
      </div>}
    />

    {message && <div className="rrugc-stage3-message">{message}</div>}

    <div className="rrugc-stage-search-toolbar rrugc-stage4-search-toolbar">
      <RrugcSmartSearchInput
        stageId="stage5"
        query={query}
        onQueryChange={onQueryChange}
        suggestions={searchSuggestions}
        placeholder="Search folder, image, reviewer, or scene…"
        label="Search Stage 5 UGC reviews"
      />
      <span>{normalizedQuery ? visibleGroups.length + " matching folders" : data.total_groups + " folders"}</span>
    </div>

    {loading ? (
      <div className="rrugc-stage3-skeletons" aria-label="Loading Stage 5 groups">
        {Array.from({ length: 4 }, (_, index) => (
          <div className="rrugc-stage3-skeleton-card" key={index}>
            <span />
            <span />
          </div>
        ))}
      </div>
    ) : visibleGroups.length === 0 ? (
      <div className="rrugc-stage3-empty">
        <strong>{normalizedQuery ? "No reviews match this search" : "No Stage 4 outputs yet"}</strong>
        <span>{normalizedQuery ? "Try a folder, source image, reviewer, scene, or status from the recommendations." : "Completed Stage 4 images will appear here automatically, grouped by folder."}</span>
      </div>
    ) : (
      <div className="rrugc-stage3-groups">
        {visibleGroups.map(group => (
          <article className="rrugc-stage3-group" key={group.folder_id}>
            <div className="rrugc-stage3-group-head">
              <div>
                <div className="rrugc-stage3-folder-title">
                  <span aria-hidden="true">▱</span>
                  <strong>{group.folder_name}</strong>
                  <em>{group.image_count} image{group.image_count === 1 ? "" : "s"}</em>
                </div>
                <small>{group.folder_path || "Root folder"}</small>
              </div>
              <div className="rrugc-stage3-group-actions">
                <div className={"rrugc-stage3-group-status is-" + group.status}>
                  <i aria-hidden="true" />
                  {groupStatusLabel(group)}
                  <span>
                    {group.ready_count} ready
                    {group.rejected_count ? " · " + group.rejected_count + " rejected" : ""}
                    {group.analyzing_count ? " · " + group.analyzing_count + " running" : ""}
                  </span>
                </div>
                <button
                  type="button"
                  className="rrugc-stage3-analyze-folder"
                  onClick={() => onAnalyze(group.folder_id)}
                  disabled={analyzing}
                >
                  Analyze folder
                </button>
              </div>
            </div>

            <div className="rrugc-stage3-gallery">
              {group.images.map(image => {
                const reviewReady = hasReview(image);
                return <figure
                  key={image.stage2_job_id}
                  className={
                    "rrugc-stage3-image is-" + image.analysis_status
                    + (reviewReady ? " is-clickable" : "")
                  }
                  role={reviewReady ? "button" : undefined}
                  tabIndex={reviewReady ? 0 : undefined}
                  aria-label={reviewReady ? "Open review details for " + image.reviewer_name : undefined}
                  onClick={reviewReady ? () => setSelectedReviewId(image.stage2_job_id) : undefined}
                  onKeyDown={reviewReady ? event => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedReviewId(image.stage2_job_id);
                    }
                  } : undefined}
                >
                  <div className="rrugc-stage3-image-media">
                    <img
                      src={image.preview_url}
                      alt={image.source_name ? "Stage 4 output for " + image.source_name : "Stage 4 output"}
                      loading="lazy"
                      decoding="async"
                    />
                    <span className={"rrugc-stage3-status-badge is-" + image.analysis_status}>
                      {statusLabel(image.analysis_status)}
                    </span>
                    {image.final_score != null && (
                      <strong className="rrugc-stage3-final-score">{percent(image.final_score)}</strong>
                    )}
                  </div>
                  <figcaption>
                    <div className="rrugc-stage3-image-title">
                      <strong>{image.source_name}</strong>
                      <span>{image.scene_type || image.framing_type || ""}</span>
                    </div>
                    {(image.analysis_status === "ready" || image.analysis_status === "rejected") && (
                      <div className="rrugc-stage3-score-row">
                        <span>UGC <b>{percent(image.mobile_ugc_score)}</b></span>
                        <span>Photo <b>{percent(image.photorealism_score)}</b></span>
                        <span>Product <b>{percent(image.product_visibility_score)}</b></span>
                        <span>Review <b>{percent(image.review_fit_score)}</b></span>
                      </div>
                    )}
                    {reviewReady ? (
                      <div className="rrugc-stage3-review-copy">
                        <div>
                          <strong>{image.reviewer_name}</strong>
                          <span
                            className="rrugc-stage3-stars"
                            aria-label={(image.star_rating || 0) + " out of 5 stars"}
                          >
                            {"★".repeat(image.star_rating || 0)}{"☆".repeat(5 - (image.star_rating || 0))}
                          </span>
                        </div>
                        <blockquote title={image.review_text || ""}>{image.review_text}</blockquote>
                        <small className="rrugc-stage3-synthetic-notice">AI-generated review concept · Not a real customer testimonial</small>
                      </div>
                    ) : (
                      <p title={imageDetail(image)}>{imageDetail(image)}</p>
                    )}
                  </figcaption>
                </figure>;
              })}
            </div>

            {group.latest_completed_at && (
              <footer className="rrugc-stage3-group-foot">
                Latest Stage 4 output: {completedLabel(group.latest_completed_at)}
              </footer>
            )}
          </article>
        ))}
      </div>
    )}

    {selectedReviewIndex >= 0 && (
      <Stage3ReviewModal
        entries={reviewEntries}
        index={selectedReviewIndex}
        onIndexChange={nextIndex => setSelectedReviewId(
          reviewEntries[nextIndex]?.image.stage2_job_id || null,
        )}
        onClose={() => setSelectedReviewId(null)}
      />
    )}
  </section>;
}
