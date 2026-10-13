import { useState, type CSSProperties } from "react";
import { DeferredImage } from "./DeferredImage";

type ComparisonMode = "after" | "before" | "compare";

/** Compare saved Pinterest lifestyle reference(s) with final Stage 4 hat-swap UGC. */
export function Stage5BeforeAfter({
  beforeUrl,
  beforeUrls,
  afterUrl,
  originalUrl,
  alt,
}: {
  beforeUrl?: string | null;
  beforeUrls?: string[];
  afterUrl: string;
  originalUrl: string;
  alt: string;
}) {
  const [mode, setMode] = useState<ComparisonMode>("after");
  const [split, setSplit] = useState(50);
  const [referenceIndex, setReferenceIndex] = useState(0);
  const choices = beforeUrls?.length ? beforeUrls : beforeUrl ? [beforeUrl] : [];
  const selectedBeforeUrl = choices[referenceIndex] || null;
  const [beforeFailed, setBeforeFailed] = useState(false);
  const [beforeLoaded, setBeforeLoaded] = useState(false);
  const canCompare = Boolean(selectedBeforeUrl) && !beforeFailed;
  const beforeShown = canCompare && mode !== "after";
  const style = { "--stage5-before-split": split + "%" } as CSSProperties;

  return <div className={"rrugc-stage5-ba is-" + mode} style={style}>
    <div className="rrugc-stage5-ba-frame">
      <DeferredImage src={afterUrl} data-original-src={originalUrl}
        className="rrugc-stage5-ba-after" alt={alt}
        rootMargin="300px 0px" loading="lazy" />
      {selectedBeforeUrl && !beforeFailed && <img
        key={selectedBeforeUrl}
        className={"rrugc-stage5-ba-before" + (beforeLoaded ? " is-loaded" : "")}
        src={selectedBeforeUrl} alt={"Before: Pinterest lifestyle reference " + (referenceIndex + 1)}
        loading="eager" decoding="async"
        onLoad={() => setBeforeLoaded(true)}
        onError={() => { setBeforeFailed(true); setMode("after"); }}
      />}
      {beforeShown && !beforeLoaded && <span className="rrugc-stage5-ba-loading" role="status">
        Loading saved Pinterest photo…
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
        <span className="rrugc-stage5-ba-label is-before">Pinterest · Before</span>
        <span className="rrugc-stage5-ba-label is-after">Generated · After</span>
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
    {choices.length > 1 && <label className="rrugc-stage5-ba-reference-choice">
      Pinterest reference
      <select aria-label="Pinterest reference to compare" value={referenceIndex}
        onChange={event => {
          setReferenceIndex(Number(event.target.value));
          setBeforeLoaded(false);
          setBeforeFailed(false);
        }}>
        {choices.map((_, index) => <option key={index} value={index}>
          {index + 1} / {choices.length}
        </option>)}
      </select>
    </label>}
    {!canCompare && <span className="rrugc-stage5-ba-unavailable" role="status">
      {beforeFailed ? "Saved Pinterest reference could not be loaded." : "Saved Pinterest reference unavailable for this job."}
    </span>}
  </div>;
}
