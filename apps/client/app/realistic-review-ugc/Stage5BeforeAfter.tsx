import { useState, type CSSProperties } from "react";
import { DeferredImage } from "./DeferredImage";

type ComparisonMode = "after" | "before" | "compare";

/** Two persisted job inputs; never guess a Before photo from a filename. */
export function Stage5BeforeAfter({
  beforeUrl,
  afterUrl,
  originalUrl,
  alt,
}: {
  beforeUrl?: string | null;
  afterUrl: string;
  originalUrl: string;
  alt: string;
}) {
  const [mode, setMode] = useState<ComparisonMode>("after");
  const [split, setSplit] = useState(50);
  const [beforeFailed, setBeforeFailed] = useState(false);
  const [beforeLoaded, setBeforeLoaded] = useState(false);
  const canCompare = Boolean(beforeUrl) && !beforeFailed;
  const beforeShown = canCompare && mode !== "after";
  const style = { "--stage5-before-split": split + "%" } as CSSProperties;

  return <div className={"rrugc-stage5-ba is-" + mode} style={style}>
    <div className="rrugc-stage5-ba-frame">
      <DeferredImage src={afterUrl} data-original-src={originalUrl}
        className="rrugc-stage5-ba-after" alt={alt}
        rootMargin="300px 0px" loading="lazy" />
      {beforeUrl && !beforeFailed && <img
        className={"rrugc-stage5-ba-before" + (beforeLoaded ? " is-loaded" : "")}
        src={beforeUrl} alt="Before: exact hat input used for Stage 4 generation"
        loading="eager" decoding="async"
        onLoad={() => setBeforeLoaded(true)}
        onError={() => { setBeforeFailed(true); setMode("after"); }}
      />}
      {beforeShown && !beforeLoaded && <span className="rrugc-stage5-ba-loading" role="status">
        Loading original hat…
      </span>}
      {mode === "compare" && canCompare && beforeLoaded && <>
        <span className="rrugc-stage5-ba-divider" aria-hidden="true">
          <span>‹ ›</span>
        </span>
        <input className="rrugc-stage5-ba-range" type="range"
          min={0} max={100} step={1} value={split}
          aria-label="Before and after comparison position"
          aria-valuetext={split + "% before photo visible"}
          onChange={event => setSplit(Number(event.target.value))}
          onKeyDown={event => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") event.stopPropagation();
          }}
        />
        <span className="rrugc-stage5-ba-label is-before">Before</span>
        <span className="rrugc-stage5-ba-label is-after">After</span>
      </>}
    </div>
    <div className="rrugc-stage5-ba-switch" role="group" aria-label="Image comparison mode">
      {(["after", "before", "compare"] as const).map(value => (
        <button key={value} type="button"
          className={mode === value ? "is-selected" : ""}
          aria-pressed={mode === value}
          disabled={value !== "after" && !canCompare}
          onClick={() => setMode(value)}>
          {value === "after" ? "After" : value === "before" ? "Before" : "Compare"}
        </button>
      ))}
    </div>
    {!canCompare && <span className="rrugc-stage5-ba-unavailable" role="status">
      {beforeFailed ? "Original hat photo could not be loaded." : "Original hat photo unavailable for this job."}
    </span>}
  </div>;
}
