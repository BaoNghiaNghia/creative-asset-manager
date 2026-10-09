import { useEffect, useState } from "react";
import { listGenerationOutputVersions, type GenerationOutputVersion } from "./api";

function SelectionIcon({ mode }: { mode: "all" | "none" }) {
  return mode === "all"
    ? <svg data-version-action="select-all" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="m7 12 3 3 6-6" /></svg>
    : <svg data-version-action="deselect" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 9l6 6m0-6-6 6" /></svg>;
}

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
  const [selectedVersions, setSelectedVersions] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setVersions([]);
    setSelectedVersions(new Set());
    void listGenerationOutputVersions(stage, jobId, controller.signal)
      .then(data => {
        if (!controller.signal.aborted) {
          setVersions(data.versions);
          setSelectedVersions(new Set(data.versions.map(item => item.version)));
        }
      })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Failed to load versions."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [stage, jobId]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handler);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handler);
    };
  }, [onClose]);

  const compared = versions.filter(version => selectedVersions.has(version.version));

  return <div className="rrugc-skill-modal-backdrop" role="presentation"
    onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className="rrugc-job-log-dialog rrugc-output-version-dialog" role="dialog" aria-modal="true"
      aria-labelledby="rrugc-output-version-title">
      <header className="rrugc-version-dialog-heading">
        <div>
          <small>{stage.toUpperCase()} · OUTPUT HISTORY</small>
          <h2 id="rrugc-output-version-title" title={title}>{title}</h2>
          <p>{versions.length} saved versions · Earlier outputs are preserved</p>
        </div>
        <button type="button" aria-label="Close output versions" onClick={onClose}>×</button>
      </header>

      <div className="rrugc-version-compare-toolbar">
        <span className="rrugc-version-compare-title"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="7" height="16" rx="1.5" /><rect x="14" y="4" width="7" height="16" rx="1.5" /></svg>
          Compare versions <strong>{compared.length}/{versions.length}</strong>
        </span>
        <div className="rrugc-version-compare-actions" role="group" aria-label="Select output versions">
          <button type="button" title="Select all versions" aria-label="Select all versions"
            disabled={loading || compared.length === versions.length || versions.length === 0}
            onClick={() => setSelectedVersions(new Set(versions.map(item => item.version)))}><SelectionIcon mode="all" /></button>
          <button type="button" title="Deselect all versions" aria-label="Deselect all versions"
            disabled={loading || compared.length === 0}
            onClick={() => setSelectedVersions(new Set())}><SelectionIcon mode="none" /></button>
        </div>
        {onRegenerate && <button type="button" className="rrugc-version-create" onClick={onRegenerate}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5v14m-7-7h14" /></svg>
          New version
        </button>}
      </div>

      {!loading && !error && versions.length > 1 && <div className="rrugc-version-filter" role="group" aria-label="Versions to compare">
        {versions.map((version, index) => <button type="button" key={version.version}
          aria-label={"Compare version " + version.version} aria-pressed={selectedVersions.has(version.version)}
          className={selectedVersions.has(version.version) ? "is-selected" : ""}
          onClick={() => setSelectedVersions(current => {
            const next = new Set(current);
            if (next.has(version.version)) next.delete(version.version);
            else next.add(version.version);
            return next;
          })}>
          <span aria-hidden="true">{selectedVersions.has(version.version) ? "✓" : "+"}</span>
          v{version.version}{version.output_role === "design_concepts" ? " · Concepts" : version.output_role === "colorways" ? " · 13 colors" : ""}{index === 0 ? " · Latest" : ""}
        </button>)}
      </div>}

      {loading && <p role="status" className="rrugc-version-message">Loading saved output versions…</p>}
      {error && <p role="alert" className="rrugc-source-error">{error}</p>}
      {!loading && !error && <div className="rrugc-version-grid" aria-label="Side-by-side version comparison">
        {compared.map(version => <article key={version.version} className="rrugc-version-compare-card">
          <div className="rrugc-version-card-heading">
            <strong>{version.output_role === "design_concepts" ? "10 Concepts + Hero" : version.output_role === "colorways" ? "13 Hat Colorways" : "Version " + version.version}</strong>
            {version.version === versions[0]?.version && <span>Latest</span>}
          </div>
          <a className="rrugc-version-image-link" href={version.url} target="_blank" rel="noreferrer" title={"Open version " + version.version}>
            <img src={version.url + "?thumbnail=true&size=400"} loading="lazy" alt={"Output version " + version.version} />
          </a>
          <div className="rrugc-version-card-meta">
            <small>{new Date(version.created_at).toLocaleString()}</small>
            {version.width && version.height && <small>{version.width}×{version.height}</small>}
          </div>
          <a className="rrugc-version-full-link" href={version.url} target="_blank" rel="noreferrer">View full image ↗</a>
        </article>)}
        {versions.length === 0 && <p className="rrugc-version-empty">No completed output version is available yet.</p>}
        {versions.length > 0 && compared.length === 0 && <p className="rrugc-version-empty">Select versions above to compare them side by side.</p>}
      </div>}
    </section>
  </div>;
}
