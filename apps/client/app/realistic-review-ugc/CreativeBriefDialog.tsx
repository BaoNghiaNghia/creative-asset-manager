import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { generateKeywordCreativeBrief, getKeywordCreativeBrief } from "./api";
import { RrugcActionIcon } from "./RrugcActionIcon";
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
  { number: "01", title: "Typography-First", description: "Clear, familiar lettering that reads instantly" },
  { number: "02", title: "Creative Twist", description: "One meaningful interaction with the quote" },
  { number: "03", title: "Explore", description: "An unexpected visual approach worth testing" },
];

function ConceptField({ title, children }: { title: string; children: string }) {
  return <div className="rrugc-creative-brief-field">
    <dt>{title}</dt>
    <dd>{children}</dd>
  </div>;
}

export function CreativeBriefDialog({ keywordId, keyword, onClose }: {
  keywordId: string;
  keyword: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<KeywordCreativeBriefDetail | null>(null);
  const [selected, setSelected] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
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
    }, 5000);
    return () => { alive = false; controller.abort(); window.clearInterval(poll); };
  }, [keywordId, detail?.status]);

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
  const concept = brief?.concepts[selected] || brief?.concepts[0];
  const generating = detail?.status === "queued" || detail?.status === "running";
  const failed = detail?.status === "failed";
  const statusLabel = brief ? "Brief ready" : generating
    ? (detail?.status === "queued" ? "In queue" : "Analyzing")
    : failed ? "Needs retry" : !detail ? (error ? "Unavailable" : "Loading brief") : "Ready to analyze";
  const statusClass = brief ? "ready" : generating || (!detail && !error) ? "working" : failed || (!detail && error) ? "failed" : "idle";

  return createPortal(
    <div className="rrugc-creative-brief-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section ref={dialogRef} role="dialog" aria-modal="true"
        aria-labelledby="rrugc-creative-brief-title" tabIndex={-1}
        onKeyDown={onDialogKeyDown} className="rrugc-creative-brief-dialog">
        <header className="rrugc-creative-brief-heading">
          <div className="rrugc-creative-brief-heading-icon"><RrugcActionIcon name="sparkles" /></div>
          <div className="rrugc-creative-brief-heading-copy">
            <span className="rrugc-creative-brief-eyebrow">STAGE 1 <span aria-hidden="true">/</span> MEANING-FIRST CREATIVE BRIEF</span>
            <h2 id="rrugc-creative-brief-title">{keyword}</h2>
            <p>Meaning and design directions for your keyword.</p>
          </div>
          <span className={"rrugc-creative-brief-status " + statusClass} role="status">
            <span className="rrugc-creative-brief-status-dot" aria-hidden="true" />{statusLabel}
          </span>
          <button type="button" className="rrugc-creative-brief-close" aria-label="Close Creative Brief" onClick={onClose}>
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div className="rrugc-creative-brief-content">
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
                ? detail.status === "queued" ? "Your brief is in the queue." : "Turning meaning into creative directions."
                : failed ? "The analysis didn't finish." : "A stronger idea starts with the meaning."}</h3>
              <p className="rrugc-creative-brief-pre-description">
                {generating
                  ? "Codex is preparing a text-only creative plan. This view updates automatically, and you can close it while the job runs."
                  : failed ? "Your existing artwork is safe. Retry the text-only analysis to prepare a new creative brief."
                    : "Explore what the quote really communicates, who it resonates with, and how it can become compelling embroidery."}
              </p>

              {generating ? <div className="rrugc-creative-brief-progress" role="status">
                <span className="rrugc-creative-brief-progress-indicator" aria-hidden="true" />
                <div><strong>{detail.status === "queued" ? "Waiting for a Codex worker" : "Codex is analyzing the keyword"}</strong>
                  <small>No images are being generated.</small></div>
              </div> : <>
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
                <strong>3 distinct concept routes</strong>
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
            <section className="rrugc-creative-brief-summary" aria-label="Creative Brief insights">
              <div className="rrugc-creative-brief-summary-lead">
                <span className="rrugc-creative-brief-section-tag">01 / CREATIVE STRATEGY</span>
                <h3>{brief.strategy}</h3>
                <span className="rrugc-creative-brief-summary-divider" aria-hidden="true" />
                <span className="rrugc-creative-brief-summary-label">THE CREATIVE HOOK</span>
                <p>{brief.creative_hook}</p>
              </div>
              <dl className="rrugc-creative-brief-insights">
                <div><dt>Meaning &amp; emotion</dt><dd>{brief.meaning_emotion}</dd></div>
                <div><dt>Target audience</dt><dd>{brief.target_audience}</dd></div>
                <div><dt>Purchase motivation</dt><dd>{brief.purchase_motivation}</dd></div>
              </dl>
            </section>

            <section className="rrugc-creative-brief-routes" aria-label="Concept proposals">
              <div className="rrugc-creative-brief-section-heading">
                <div><span className="rrugc-creative-brief-section-tag">02 / CONCEPT DIRECTIONS</span>
                  <h3>Three ways to bring this idea to life</h3></div>
                <span>{brief.concepts.length} directions · Select to compare</span>
              </div>
              <div className="rrugc-creative-brief-workspace">
                <div className="rrugc-creative-brief-proposals" role="group" aria-label="Select a concept">
                  {brief.concepts.map((item, index) => {
                    const mode = CONCEPT_MODES[item.mode];
                    return <button type="button" key={item.mode} onClick={() => setSelected(index)}
                      className={"rrugc-creative-brief-proposal" + (concept === item ? " active" : "")}
                      aria-pressed={concept === item}
                      aria-label={mode.label + ": " + item.name}>
                      <span className="rrugc-creative-brief-proposal-top">
                        <span className="rrugc-creative-brief-proposal-number">{mode.number}</span>
                        <span className="rrugc-creative-brief-proposal-mode">{mode.label}</span>
                        <RrugcActionIcon name="chevron-right" />
                      </span>
                      <strong>{item.name}</strong>
                      <small>{item.description}</small>
                    </button>;
                  })}
                </div>

                {concept && <article className="rrugc-creative-brief-concept" aria-live="polite">
                  <header className="rrugc-creative-brief-concept-heading">
                    <span className="rrugc-creative-brief-concept-eyebrow">SELECTED DIRECTION · {CONCEPT_MODES[concept.mode].label}</span>
                    <h4>{concept.name}</h4>
                    <p>{CONCEPT_MODES[concept.mode].hint}</p>
                  </header>
                  <div className="rrugc-creative-brief-concept-grid">
                    <div className="rrugc-creative-brief-detail-section">
                      <h5><span>01</span> Visual idea</h5>
                      <dl>
                        <ConceptField title="Familiar format">{concept.familiar_format}</ConceptField>
                        <ConceptField title="The twist">{concept.creative_twist}</ConceptField>
                        <ConceptField title="Creative hook">{concept.creative_hook}</ConceptField>
                      </dl>
                    </div>
                    <div className="rrugc-creative-brief-detail-section">
                      <h5><span>02</span> Make it real</h5>
                      <dl>
                        <ConceptField title="Typography">{concept.typography_plan}</ConceptField>
                        <ConceptField title="Embroidery plan">{concept.embroidery_plan}</ConceptField>
                        <ConceptField title="2-second clarity">{concept.clarity_assessment}</ConceptField>
                        <ConceptField title="Embroidery fit">{concept.embroidery_fit}</ConceptField>
                      </dl>
                    </div>
                  </div>
                  {concept.risks.length > 0 && <div className="rrugc-creative-brief-risks">
                    <strong>Watch-outs</strong>
                    <ul>{concept.risks.map((risk, index) => <li key={index}>{risk}</li>)}</ul>
                  </div>}
                </article>}
              </div>
              <p className="rrugc-creative-brief-disclaimer">
                AI planning guidance only. Concepts are not applied to your generation skill; check the artwork for embroidery suitability.
              </p>
            </section>
          </>}
        </div>
        <footer className="rrugc-creative-brief-footer">
          <span className="rrugc-creative-brief-footer-meta">
            <RrugcActionIcon name="check" />
            <span>Text-only · Existing artwork stays unchanged</span>
            <span className="rrugc-creative-brief-footer-version">{detail?.prompt_version || "Meaning-First V1"}</span>
          </span>
          <button type="button" onClick={onClose}>Close</button>
        </footer>
      </section>
    </div>, document.body,
  );
}
