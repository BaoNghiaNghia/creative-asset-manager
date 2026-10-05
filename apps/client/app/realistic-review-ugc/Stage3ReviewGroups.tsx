import type { Stage3ReviewGroupList } from "./types";
import "./Stage3ReviewGroups.css";

type Props = {
  data: Stage3ReviewGroupList;
  loading: boolean;
};

function completedLabel(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

export function Stage3ReviewGroups({ data, loading }: Props) {
  return <section className="rrugc-stage3">
    <header className="rrugc-stage3-header">
      <div>
        <p className="rrugc-stage-kicker">STAGE 3 · FOLDER GROUPS → UGC REVIEW</p>
        <h2>UGC Review</h2>
        <p>
          Stage 2 outputs are grouped by their real destination folder.
          Each folder becomes one review group for the next automated UGC step.
        </p>
      </div>
      <div className="rrugc-stage3-stats" aria-label="Stage 3 summary">
        <span><strong>{data.total_groups}</strong><small>Folders</small></span>
        <span><strong>{data.total_images}</strong><small>Stage 2 images</small></span>
      </div>
    </header>

    {loading ? (
      <div className="rrugc-stage3-skeletons" aria-label="Loading Stage 3 groups">
        {Array.from({ length: 4 }, (_, index) => (
          <div className="rrugc-stage3-skeleton-card" key={index}>
            <span />
            <span />
          </div>
        ))}
      </div>
    ) : data.items.length === 0 ? (
      <div className="rrugc-stage3-empty">
        <strong>No Stage 2 outputs yet</strong>
        <span>Completed Stage 2 images will appear here automatically, grouped by folder.</span>
      </div>
    ) : (
      <div className="rrugc-stage3-groups">
        {data.items.map(group => (
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
              <div className="rrugc-stage3-ready">
                <i aria-hidden="true" />
                Ready for UGC review
              </div>
            </div>

            <div className="rrugc-stage3-gallery">
              {group.images.map(image => (
                <figure key={image.stage2_job_id} className="rrugc-stage3-image">
                  <img
                    src={image.preview_url}
                    alt={image.source_name ? "Stage 2 output for " + image.source_name : "Stage 2 output"}
                    loading="lazy"
                    decoding="async"
                  />
                  <figcaption>
                    <strong>{image.source_name}</strong>
                    <span>
                      {image.output_width && image.output_height
                        ? image.output_width + "×" + image.output_height
                        : "Generated image"}
                    </span>
                  </figcaption>
                </figure>
              ))}
            </div>

            {group.latest_completed_at && (
              <footer className="rrugc-stage3-group-foot">
                Latest output: {completedLabel(group.latest_completed_at)}
              </footer>
            )}
          </article>
        ))}
      </div>
    )}
  </section>;
}
