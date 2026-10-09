import { useEffect, useState } from "react";
import { listGenerationOutputVersions, type GenerationOutputVersion } from "./api";

export function GenerationOutputVersionsDialog({
  stage, jobId, title, onClose, onRegenerate,
}: {
  stage: "stage1" | "stage2" | "stage4";
  jobId: string;
  title: string;
  onClose: () => void;
  onRegenerate?: () => void;
}) {
  const [versions, setVersions] = useState<GenerationOutputVersion[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void listGenerationOutputVersions(stage, jobId, controller.signal)
      .then(data => { if (!controller.signal.aborted) setVersions(data.versions); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Failed to load versions."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [stage, jobId]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
  return <div className="rrugc-skill-modal-backdrop" role="presentation"
    onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className="rrugc-job-log-dialog rrugc-output-version-dialog" role="dialog" aria-modal="true"
      aria-labelledby="rrugc-output-version-title">
      <header>
        <div>
          <small>{stage.toUpperCase()} · OUTPUT HISTORY</small>
          <h2 id="rrugc-output-version-title">{title}</h2>
          <p>Every successful generation is kept as an immutable version. Re-running does not remove earlier images.</p>
        </div>
        <button type="button" aria-label="Close output versions" onClick={onClose}>×</button>
      </header>
      {onRegenerate && <button type="button" className="rrugc-primary" onClick={onRegenerate}>Generate new version</button>}
      {loading && <p role="status">Loading saved output versions…</p>}
      {error && <p role="alert" className="rrugc-source-error">{error}</p>}
      {!loading && !error && <div className="rrugc-version-grid">
        {versions.map((version, index) => <article key={version.version}>
          <a href={version.url} target="_blank" rel="noreferrer" title={"Open version " + version.version}>
            <img src={version.url + "?thumbnail=true&size=300"} loading="lazy"
              alt={"Output version " + version.version} />
          </a>
          <div><strong>Output v{version.version} {index === 0 ? "· Latest" : ""}</strong>
            <small>{new Date(version.created_at).toLocaleString()} {version.width && version.height ? "· " + version.width + "×" + version.height : ""}</small>
          </div>
          <a href={version.url} target="_blank" rel="noreferrer">View full image ↗</a>
        </article>)}
        {!versions.length && <p>No completed output version is available yet.</p>}
      </div>}
    </section>
  </div>;
}
