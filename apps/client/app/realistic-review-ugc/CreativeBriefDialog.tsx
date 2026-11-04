import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import {
  generateKeywordCreativeBrief, getKeywordCreativeBrief,
  getCreativeV3GenerationStatus, generateCreativeV3Stage1Images, regenerateCreativeV3Stage1Images,
} from "./api";
import { RrugcActionIcon } from "./RrugcActionIcon";
import type { CreativeV3DesignStyle } from "./api";
import type { KeywordCreativeBriefDetail, KeywordCreativeConcept } from "./types";
import "./CreativeBriefDialog.css";

const CONCEPT_MODES: Record<KeywordCreativeConcept["mode"], {
  label: string; hint: string; number: string;
}> = {
  safe: {
    label: "Typography-First",
    hint: "Clear, familiar and immediately readable",
    number: "01",
  },
  creative_twist: {
    label: "Creative Twist",
    hint: "An unexpected connection with the meaning",
    number: "02",
  },
  explore: {
    label: "Explore",
    hint: "A more experimental visual direction",
    number: "03",
  },
};

const ANALYSIS_STEPS = [
  { number: "01", title: "Typography-First · 1 design", description: "Clear, familiar lettering that reads instantly" },
  { number: "02", title: "Creative Twist · 4 designs", description: "Four different meaning-led interactions, not recolors" },
  { number: "03", title: "Explore · 4 designs", description: "Four experimental layouts and stitch directions" },
];

function ConceptField({ title, children }: { title: string; children: string }) {
  return <div className="rrugc-creative-brief-field">
    <dt>{title}</dt>
    <dd>{children}</dd>
  </div>;
}

export function CreativeBriefDialog({ keywordId, keyword, onClose, hasExistingStage1Job = false, stage1Status = "not_run" }: {
  keywordId: string;
  keyword: string;
  onClose: () => void;
  hasExistingStage1Job?: boolean;
  stage1Status?: "not_run" | "queued" | "running" | "completed" | "failed";
}) {
  const [detail, setDetail] = useState<KeywordCreativeBriefDetail | null>(null);
  const [selectedDirection, setSelectedDirection] = useState(0);
  const [designStyle, setDesignStyle] = useState<CreativeV3DesignStyle>("auto");
  const [stylePolicy, setStylePolicy] = useState({
    defaultStyle: "heritage" as "heritage" | "balanced" | "experimental",
    defaultVersion: "heritage-lettering-h1",
    policyVersion: "stage1-heritage-default-h1",
  });
  const [busy, setBusy] = useState(false);
  const [priorityRequested, setPriorityRequested] = useState(false);
  const [error, setError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [v3Available, setV3Available] = useState(false);
  const [imageBusy, setImageBusy] = useState(false);
  const [v3Queued, setV3Queued] = useState(false);
  const [confirmImageRegenerate, setConfirmImageRegenerate] = useState(false);
  const stage1Busy = stage1Status === "queued" || stage1Status === "running";
  const [showStrategyDetails, setShowStrategyDetails] = useState(false);
  const [showConceptDetails, setShowConceptDetails] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      if (previousFocusRef.current?.isConnected) previousFocusRef.current.focus();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void getCreativeV3GenerationStatus(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setV3Available(result.available && result.output_count === 9);
      if (result.default_design_style && result.default_style_version) {
        setStylePolicy({
          defaultStyle: result.default_design_style,
          defaultVersion: result.default_style_version,
          policyVersion: result.style_policy_version || result.default_style_version,
        });
      }
    }).catch(() => { if (!controller.signal.aborted) setV3Available(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    void getKeywordCreativeBrief(keywordId, controller.signal).then(result => {
      if (alive) { setDetail(result); setError(""); }
    }).catch(reason => {
      if (alive && !controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Could not load Creative Brief.");
      }
    });
    return () => { alive = false; controller.abort(); };
  }, [keywordId, refreshToken]);

  useEffect(() => {
    if (detail?.status !== "queued" && detail?.status !== "running") return;
    let alive = true;
    const controller = new AbortController();
    const poll = window.setInterval(async () => {
      if (document.hidden || !alive) return;
      try {
        const result = await getKeywordCreativeBrief(keywordId, controller.signal);
        if (alive) { setDetail(result); setError(""); }
      } catch (reason) {
        if (alive && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Could not refresh Creative Brief.");
        }
      }
    }, detail?.error_code?.startsWith("creative_brief_codex_") ? 30000 : 5000);
    return () => { alive = false; controller.abort(); window.clearInterval(poll); };
  }, [keywordId, detail?.status, detail?.error_code]);

  const analyze = async () => {
    if (busy || detail?.status === "queued" || detail?.status === "running") return;
    setBusy(true);
    setError("");
    try {
      await generateKeywordCreativeBrief(keywordId);
      setDetail(current => current ? { ...current, status: "queued", brief: null } : current);
      setRefreshToken(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue analysis.");
    } finally {
      setBusy(false);
    }
  };

  const prioritizeQueued = async () => {
    if (busy || priorityRequested || detail?.status !== "queued") return;
    setBusy(true);
    setError("");
    try {
      // Server promotes the same queued job; no duplicate or extra Codex call.
      await generateKeywordCreativeBrief(keywordId);
      setPriorityRequested(true);
      setRefreshToken(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not prioritize this brief.");
    } finally {
      setBusy(false);
    }
  };

  const generateImages = async () => {
    if (!v3Available || imageBusy || v3Queued || stage1Busy || !detail?.brief || !detail.updated_at) return;
    if (detail.brief.keyword_exact !== keyword) {
      setError("The original keyword changed. Reopen the Creative Brief before generating.");
      return;
    }
    setImageBusy(true);
    setError("");
    try {
      if (hasExistingStage1Job) {
        if (!confirmImageRegenerate) { setConfirmImageRegenerate(true); return; }
        await regenerateCreativeV3Stage1Images(keywordId, detail.updated_at, designStyle);
      } else {
        await generateCreativeV3Stage1Images(keywordId, detail.updated_at, designStyle);
      }
      setV3Queued(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not queue embroidery generation.");
    } finally {
      setImageBusy(false);
    }
  };

  const onDialogKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
    if (event.key !== "Tab") return;
    const actions = Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>(
      "button:not(:disabled)",
    ) || []);
    if (!actions.length) { event.preventDefault(); return; }
    const first = actions[0];
    const last = actions[actions.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) {
      event.preventDefault();
      first.focus();
    }
  };

  const brief = detail?.brief;
  const resolvedStyle = designStyle === "auto" ? stylePolicy.defaultStyle : designStyle;
  const styleName = resolvedStyle === "heritage" ? "Heritage Lettering" : resolvedStyle === "balanced" ? "Balanced" : "Experimental";
  const styleVersion = designStyle === "auto" ? stylePolicy.defaultVersion : resolvedStyle === "heritage"
    ? stylePolicy.defaultVersion : resolvedStyle + "-v1";
  const directions = brief?.concepts.flatMap((item, group) =>
    (item.design_variants?.length ? item.design_variants : [item.description])
      .map((direction, variant) => ({ item, group, variant, direction }))) || [];
  const activeDirection = directions[selectedDirection] || directions[0];
  const concept = activeDirection?.item;
  const automaticRetryEnabled = detail?.automatic_retry_enabled !== false;
  const generating = detail?.status === "queued" || detail?.status === "running";
  const failed = detail?.status === "failed";
  const codexWaiting = detail?.status === "queued" && Boolean(detail.error_code?.startsWith("creative_brief_codex_"));
  const statusLabel = brief ? "Brief ready" : generating
    ? (codexWaiting && !automaticRetryEnabled ? "Auto retry paused" : codexWaiting ? "Waiting for Codex" : detail?.status === "queued" ? "In queue" : "Analyzing")
    : failed ? "Needs retry" : !detail ? (error ? "Unavailable" : "Loading brief") : "Ready to analyze";
  const statusClass = brief ? "ready" : generating || (!detail && !error) ? "working" : failed || (!detail && error) ? "failed" : "idle";

  return createPortal(
    <div className="rrugc-creative-brief-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section ref={dialogRef} role="dialog" aria-modal="true"
        aria-labelledby="rrugc-creative-brief-title" tabIndex={-1}
        onKeyDown={onDialogKeyDown} className={"rrugc-creative-brief-dialog " + (brief ? "is-complete" : "is-pending")}>
        <header className="rrugc-creative-brief-heading">
          <div className="rrugc-creative-brief-heading-icon"><RrugcActionIcon name="sparkles" /></div>
          <div className="rrugc-creative-brief-heading-copy">
            <span className="rrugc-creative-brief-eyebrow">STAGE 1 <span aria-hidden="true">/</span> CREATIVE BRIEF LITE V3</span>
            <h2 id="rrugc-creative-brief-title">{keyword}</h2>
          </div>
          <span className={"rrugc-creative-brief-status " + statusClass} role="status">
            <span className="rrugc-creative-brief-status-dot" aria-hidden="true" />{statusLabel}
          </span>
          <button type="button" className="rrugc-creative-brief-close" aria-label="Close Creative Brief" onClick={onClose}>
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div className="rrugc-creative-brief-content">
          <div className="rrugc-creative-brief-active-style" role="status" title={stylePolicy.policyVersion}>
            <strong>Default style: {stylePolicy.defaultStyle === "heritage" ? "Heritage Lettering" : stylePolicy.defaultStyle === "balanced" ? "Balanced" : "Experimental"} · {stylePolicy.defaultVersion}</strong>
            <span>New Generate and Regenerate use the latest style policy. Retry keeps the previous job's style.</span>
          </div>
          {error && <div className="rrugc-creative-brief-alert" role="alert">
            <strong>Something went wrong.</strong><span>{error}</span>
            {!detail && <button type="button" onClick={() => { setError(""); setRefreshToken(value => value + 1); }}>
              Try loading again
            </button>}
          </div>}
          {!detail && !error && <div className="rrugc-creative-brief-loading" role="status">
            <span className="rrugc-creative-brief-loading-symbol"><RrugcActionIcon name="sparkles" /></span>
            <div><strong>Loading Creative Brief</strong><p>Retrieving the latest keyword analysis…</p></div>
            <span className="rrugc-creative-brief-loading-bar" aria-hidden="true" />
          </div>}

          {detail && !brief && <div className={"rrugc-creative-brief-pre " + (generating ? "is-working" : "")}>
            <div className="rrugc-creative-brief-pre-main">
              <span className="rrugc-creative-brief-section-tag">
                {generating ? "ANALYSIS IN PROGRESS" : failed ? "ANALYSIS NEEDS ATTENTION" : "START WITH INTENT"}
              </span>
              <h3>{generating
                ? codexWaiting ? (automaticRetryEnabled ? "Codex is temporarily unavailable." : "Automatic Creative Brief retry is paused.") : detail.status === "queued" ? "Your brief is in the queue." : "Turning meaning into creative directions."
                : failed ? "The analysis didn't finish." : "A stronger idea starts with the meaning."}</h3>
              <p className="rrugc-creative-brief-pre-description">
                {generating
                  ? codexWaiting
                    ? automaticRetryEnabled
                      ? "The system will retry automatically once Codex quota is available. No manual Retry is needed; existing Stage 1 designs are preserved."
                      : "No automatic Codex retry will run. The current queued attempt will end without another Codex call; use Retry analysis manually afterward. Existing images remain unchanged."
                    : "Codex is preparing a text-only creative plan. This view updates automatically, and you can close it while the job runs."
                  : failed ? "Your existing artwork is safe. Retry the text-only analysis to prepare a new creative brief."
                    : "Explore what the quote really communicates, who it resonates with, and how it can become compelling embroidery."}
              </p>

              {generating ? <>
                <div className="rrugc-creative-brief-progress" role="status">
                  <span className="rrugc-creative-brief-progress-indicator" aria-hidden="true" />
                  <div><strong>{codexWaiting ? automaticRetryEnabled ? "Waiting for Codex quota · automatic retry" : "Automatic retry paused · manual retry required"
                      : detail.status === "queued" ? "Waiting for a Codex worker" : "Codex is analyzing the keyword"}</strong>
                    <small>{codexWaiting && automaticRetryEnabled && detail.next_retry_at
                      ? "Next automatic check: " + new Date(detail.next_retry_at).toLocaleString()
                      : "No images are being generated."}</small></div>
                </div>
                {detail.status === "queued" && !codexWaiting && <button type="button"
                  className="rrugc-creative-brief-prioritize" onClick={() => void prioritizeQueued()}
                  disabled={busy || priorityRequested}
                  title="Move this existing queued analysis ahead of background backfill; no duplicate job is created">
                  {busy ? "Prioritizing…" : priorityRequested ? "Priority requested ✓" : "Prioritize this keyword"}
                </button>}
              </> : <>
                <button type="button" className="rrugc-creative-brief-primary" onClick={() => void analyze()} disabled={busy}>
                  <RrugcActionIcon name={failed ? "refresh" : "sparkles"} />
                  {busy ? "Adding to queue…" : failed ? "Retry analysis" : "Analyze keyword"}
                  {!busy && <RrugcActionIcon name="chevron-right" />}
                </button>
                <div className="rrugc-creative-brief-preserve"><RrugcActionIcon name="check" />
                  Stage 1 images and Output History remain unchanged.
                </div>
              </>}
            </div>
            <aside className="rrugc-creative-brief-preview" aria-label="Creative Brief analysis outline">
              <div className="rrugc-creative-brief-preview-header">
                <span>WHAT YOU'LL GET</span>
                <strong>9 designs · 3 creative routes</strong>
              </div>
              <div className="rrugc-creative-brief-preview-list">
                {ANALYSIS_STEPS.map((item, index) => <div key={item.number} className="rrugc-creative-brief-preview-item">
                  <span className={"rrugc-creative-brief-preview-number number-" + index}>{item.number}</span>
                  <div><strong>{item.title}</strong><p>{item.description}</p></div>
                  <span className="rrugc-creative-brief-preview-rule" aria-hidden="true" />
                </div>)}
              </div>
              <div className="rrugc-creative-brief-preview-note">
                <RrugcActionIcon name="file" /> Meaning · Typography · Embroidery fit
              </div>
            </aside>
          </div>}

          {brief && <>
            <section className={"rrugc-creative-brief-summary" + (showStrategyDetails ? " expanded" : "")} aria-label="Creative Brief insights">
              <div className="rrugc-creative-brief-summary-lead">
                <div className="rrugc-creative-brief-summary-bar">
                  <span className="rrugc-creative-brief-section-tag">01 / CREATIVE STRATEGY</span>
                  <button type="button" className="rrugc-creative-brief-summary-toggle"
                    aria-expanded={showStrategyDetails} aria-controls="rrugc-creative-brief-insights"
                    onClick={() => setShowStrategyDetails(value => !value)}>
                    {showStrategyDetails ? "Show less" : "View full insight"}
                    <RrugcActionIcon name="chevron-right" />
                  </button>
                </div>
                <h3>{brief.strategy}</h3>
                <div className="rrugc-creative-brief-summary-hook">
                  <span className="rrugc-creative-brief-summary-label">CREATIVE HOOK</span>
                  <p>{brief.creative_hook}</p>
                </div>
              </div>
              <dl className="rrugc-creative-brief-insights" id="rrugc-creative-brief-insights" hidden={!showStrategyDetails}>
                <div><dt>Meaning &amp; emotion</dt><dd>{brief.meaning_emotion}</dd></div>
                <div><dt>Target audience</dt><dd>{brief.target_audience}</dd></div>
                <div><dt>Purchase motivation</dt><dd>{brief.purchase_motivation}</dd></div>
              </dl>
            </section>

            <section className="rrugc-creative-brief-routes" aria-label="Concept proposals">
              <div className="rrugc-creative-brief-section-heading">
                <div><span className="rrugc-creative-brief-section-tag">02 / CONCEPT DIRECTIONS</span>
                  <h3>Preview 9 design directions</h3></div>
                <span>1 Typography-First · 4 Creative Twist · 4 Explore · Preview only</span>
              </div>
              <div className="rrugc-creative-brief-workspace">
                <div className="rrugc-creative-brief-proposals" role="group" aria-label="Select a concept">
                  {directions.map(({ item, variant, direction }, index) => {
                    const mode = CONCEPT_MODES[item.mode];
                    return <button type="button" key={item.mode + "-" + variant}
                      onClick={() => { setSelectedDirection(index); setShowConceptDetails(false); }}
                      className={"rrugc-creative-brief-proposal" + (activeDirection === directions[index] ? " active" : "")}
                      aria-pressed={activeDirection === directions[index]}
                      aria-label={mode.label + " direction " + (variant + 1) + ": " + item.name}>
                      <span className="rrugc-creative-brief-proposal-top">
                        <span className="rrugc-creative-brief-proposal-number">{String(index + 1).padStart(2, "0")}</span>
                        <span className="rrugc-creative-brief-proposal-mode">{mode.label} · {String.fromCharCode(65 + variant)}</span>
                        <RrugcActionIcon name="chevron-right" />
                      </span>
                      <strong>{item.name}</strong>
                      <small>{direction}</small>
                    </button>;
                  })}
                </div>

                {concept && <article className="rrugc-creative-brief-concept" aria-live="polite" id="rrugc-creative-brief-selected">
                  <header className="rrugc-creative-brief-concept-heading">
                    <div>
                      <span className="rrugc-creative-brief-concept-eyebrow">SELECTED CONCEPT · {CONCEPT_MODES[concept.mode].number} / {CONCEPT_MODES[concept.mode].label}</span>
                      <h4>{concept.name}</h4>
                    </div>
                    <p>{CONCEPT_MODES[concept.mode].hint}</p>
                  </header>

                  <div className="rrugc-creative-brief-concept-grid">
                    <section className="rrugc-creative-brief-detail-section rrugc-creative-brief-visual-plan">
                      <h5><span>01</span> Visual direction</h5>
                      <p className="rrugc-creative-brief-idea">{activeDirection?.direction}</p>
                      <div className="rrugc-creative-brief-hook-line">
                        <strong>Creative hook</strong>
                        <p>{concept.creative_hook}</p>
                      </div>
                    </section>
                    <section className="rrugc-creative-brief-detail-section rrugc-creative-brief-production-plan">
                      <h5><span>02</span> Typography hierarchy</h5>
                      <dl>
                        <ConceptField title="Type & layout">{concept.typography_plan}</ConceptField>
                        <div className="rrugc-brief-material-profile"><strong>Material profile</strong><span>Natural Satin · Low Relief · Thread Fill</span><small>Controlled centrally by ImageGen, not by this brief</small></div>
                      </dl>
                    </section>
                  </div>
                  <div className="rrugc-creative-brief-concept-bottom">
                    <div className="rrugc-creative-brief-readability">
                      <strong>2-second readability</strong>
                      <span>{concept.clarity_assessment}</span>
                    </div>
                    <button type="button" className="rrugc-creative-brief-more"
                      aria-expanded={showConceptDetails} aria-controls="rrugc-creative-brief-more-details"
                      onClick={() => setShowConceptDetails(value => !value)}>
                      {showConceptDetails ? "Hide details" : "Full design notes"}
                      <RrugcActionIcon name="chevron-right" />
                    </button>
                  </div>
                  <div className="rrugc-creative-brief-additional" id="rrugc-creative-brief-more-details" hidden={!showConceptDetails}>
                    <dl>
                      <ConceptField title="Familiar format">{concept.familiar_format}</ConceptField>
                      <ConceptField title="Embroidery feasibility notes (non-binding)">{concept.embroidery_plan}</ConceptField>
                      <ConceptField title="Embroidery fit">{concept.embroidery_fit}</ConceptField>
                    </dl>
                  </div>
                  {concept.risks.length > 0 && <div className="rrugc-creative-brief-risks">
                    <strong>Watch-outs</strong>
                    <ul>{concept.risks.map((risk, index) => <li key={index}>{risk}</li>)}</ul>
                  </div>}
                </article>}
              </div>
              <p className="rrugc-creative-brief-disclaimer">
                Selecting a direction only changes this preview. Generate always creates all 9 designs. ImageGen applies one central embroidery material profile; visual spelling and production-fit review are still required.
              </p>
            </section>
            {v3Available && <section className="rrugc-creative-style-picker" aria-label="Select design style">
              <div className="rrugc-creative-style-heading"><strong>Design Style</strong><span>Applies to the next 9-image batch</span></div>
              <div className="rrugc-creative-style-options" role="group" aria-label="Design style">
                {([ ["auto", "Auto · " + (stylePolicy.defaultStyle === "heritage" ? "Heritage Lettering" : stylePolicy.defaultStyle === "balanced" ? "Balanced" : "Experimental"), "Latest " + stylePolicy.defaultVersion], ["balanced", "Balanced", "Mix of design approaches"], ["experimental", "Experimental", "Bolder typography architecture"] ] as const).map(([value, label, hint]) =>
                  <button key={value} type="button" aria-pressed={designStyle === value}
                    disabled={imageBusy || v3Queued || stage1Busy}
                    className={"rrugc-creative-style-option" + (designStyle === value ? " selected" : "")}
                    onClick={() => { setDesignStyle(value); setConfirmImageRegenerate(false); }}>
                    <strong>{label}</strong><small>{hint}</small>
                  </button>)}
              </div>
            </section>}
            {v3Available && <section className="rrugc-creative-v3-generate" aria-label="Generate Creative Brief artwork">
              <div>
                <strong>Creative Engine V3 · Embroidery masters</strong>
                <p>{hasExistingStage1Job ? "Regenerate a completely new 9-design V3 batch without reusing previous images." : "Generate 9 transparent Stage 1 embroidery artworks: 1 Typography-First · 4 Creative Twist · 4 Explore."} Existing Output History is preserved.</p>
                <small>Style: {styleName} · {styleVersion}{designStyle === "auto" ? " · Auto-update on next Generate / Regenerate" : " · Manual override"} · Exact keyword · Human spelling and stitch review before Stage 2</small>
              </div>
              <button type="button" className="rrugc-creative-brief-primary"
                onClick={() => void generateImages()}
                disabled={imageBusy || v3Queued || stage1Busy || !detail?.updated_at}>
                <RrugcActionIcon name="sparkles" />
                {v3Queued ? "9 images queued" : stage1Busy ? "Stage 1 job active" : imageBusy ? "Adding to queue…" : hasExistingStage1Job ? confirmImageRegenerate ? "Confirm new nine-design batch" : "Regenerate 9 designs" : "Generate 9 embroidery designs"}
              </button>
              {confirmImageRegenerate && !v3Queued && <p role="status" className="rrugc-creative-v3-queued">Confirm again to create nine fresh designs. The previous images remain available in Output History.</p>}
              {v3Queued && <p role="status" className="rrugc-creative-v3-queued">Job queued in Stage 1. You can close this brief and follow its progress in the keyword row.</p>}
            </section>}
          </>}
        </div>
        <footer className="rrugc-creative-brief-footer">
          <span className="rrugc-creative-brief-footer-meta">
            <RrugcActionIcon name="check" />
            <span>{v3Queued ? "Stage 1 ImageGen queued · Old output history preserved" : "Brief is text-only · No automatic Codex retry · Artwork unchanged"}</span>
            <span className="rrugc-creative-brief-footer-version" title={detail?.prompt_version || undefined}>Creative Brief Lite V3</span>
          </span>
        </footer>
      </section>
    </div>, document.body,
  );
}