import { useEffect, useMemo, useRef, useState } from "react";
import { getTypographyStyleLabOptions, previewTypographyStyle, saveReviewedTypographyBatch, listSavedTypographyV2, handoffTypographyV2, getCreativeV3Status, getCreativeV3Preview } from "./api";
import type { TypographySavedVersion, CreativeV3Preview } from "./api";
import type {
  TypographyReference, TypographyPreviewResponse, TypographySvgPreview,
} from "./types";
import "./TypographyStyleLab.css";

export type StyleLabKeyword = { keyword_id: string; keyword: string };

function labelFor(reference: TypographyReference): string {
  return reference.filename || reference.source_attribution || "Typography reference";
}

/** The generated SVG is displayed ONLY as an img, never injected into the DOM. */
function vectorSource(preview: TypographySvgPreview): string {
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(preview.svg);
}

export function TypographyStyleLab({
  active, selectedKeyword, onBack, onOpenIdeas,
}: {
  active: boolean;
  selectedKeyword: StyleLabKeyword | null;
  onBack: () => void;
  onOpenIdeas?: () => void;
}) {
  const [keywordId, setKeywordId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [references, setReferences] = useState<TypographyReference[]>([]);
  const [pickedKeywords, setPickedKeywords] = useState<StyleLabKeyword[]>([]);
  const [approvedCount, setApprovedCount] = useState(0);
  const [hasLoadedOptions, setHasLoadedOptions] = useState(false);
  const [autoPreview, setAutoPreview] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const autoAttemptRef = useRef("");
  const [moreReferences, setMoreReferences] = useState(false);
  const [loadingReferences, setLoadingReferences] = useState(false);
  const [previews, setPreviews] = useState<TypographyPreviewResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const [v3Available, setV3Available] = useState(false);
  const [v3Preview, setV3Preview] = useState<CreativeV3Preview | null>(null);
  const [v3Loading, setV3Loading] = useState(false);
  const [v3Error, setV3Error] = useState("");
  const [v3Mode, setV3Mode] = useState<"safe" | "creative_twist" | "explore">("creative_twist");

  const [batchId, setBatchId] = useState("");
  const [chosenSlots, setChosenSlots] = useState<number[]>([1]);
  const [savedVersions, setSavedVersions] = useState<TypographySavedVersion[]>([]);
  const [stage2Selected, setStage2Selected] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [changing, setChanging] = useState<number | null>(null);
  const [mvpMessage, setMvpMessage] = useState("");
  const refreshHistory = async () => {
    if (keywordId) setSavedVersions((await listSavedTypographyV2(keywordId)).items);
  };
  const saveReviewed = async () => {
    if (!keywordId || !referenceId || !batchId || !chosenSlots.length || saving) return;
    setSaving(true); setError(""); setMvpMessage("");
    try {
      const result = await saveReviewedTypographyBatch(batchId.trim(), {
        keyword_id: keywordId, reference_id: referenceId, selected_slots: chosenSlots,
      });
      await refreshHistory();
      setMvpMessage("Saved " + result.saved_count + " reviewed design(s).");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Save unavailable."); }
    finally { setSaving(false); }
  };
  const setStage2 = async (version: number) => {
    if (!keywordId || changing !== null) return;
    setChanging(version); setError(""); setMvpMessage("");
    const selected = !stage2Selected.includes(version);
    try {
      const result = await handoffTypographyV2(keywordId, version, selected);
      setStage2Selected(previous => result.selected
        ? [...new Set([...previous, version])]
        : previous.filter(v => v !== version));
      setMvpMessage(result.selected ? "Selected for Stage 2. No job queued." : "Removed from Stage 2.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Selection unavailable."); }
    finally { setChanging(null); }
  };


  useEffect(() => {
    if (selectedKeyword) setKeywordId(selectedKeyword.keyword_id);
  }, [selectedKeyword]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoadingReferences(true);
    setError("");
    setHasLoadedOptions(false);
    void getTypographyStyleLabOptions(selectedKeyword?.keyword_id, controller.signal)
      .then(options => {
        if (controller.signal.aborted) return;
        if (!options.preview_only || options.provider_calls !== 0) {
          throw new Error("Unsafe Style Lab options response.");
        }
        setPickedKeywords(options.keywords);
        setReferences(options.references);
        setApprovedCount(options.approved_reference_count);
        setMoreReferences(options.more_references);
        setKeywordId(previous => selectedKeyword?.keyword_id ||
          (options.keywords.some(item => item.keyword_id === previous)
            ? previous : options.keywords[0]?.keyword_id || ""));
        setReferenceId(previous => options.references.some(ref => ref.id === previous)
          ? previous : options.references[0]?.id || "");
        setHasLoadedOptions(true);
      })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Reference lookup failed.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingReferences(false); });
    return () => controller.abort();
  }, [active, selectedKeyword?.keyword_id]);

  useEffect(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy(false);
    setPreviews(null);
    setError("");
  }, [keywordId, referenceId]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  // V3 remains independently feature-gated; a normal V2 Style Lab session
  // never probes protected V3 data when the availability flag is OFF.
  useEffect(() => {
    if (!active || !advancedOpen) {
      setV3Available(false);
      return;
    }
    const controller = new AbortController();
    void getCreativeV3Status(controller.signal)
      .then(result => { if (!controller.signal.aborted) setV3Available(result.available === true); })
      .catch(() => { if (!controller.signal.aborted) setV3Available(false); });
    return () => controller.abort();
  }, [active, advancedOpen]);

  useEffect(() => {
    setV3Preview(null);
    setV3Loading(false);
    setV3Error("");
    setV3Mode("creative_twist");
    if (!active || !advancedOpen || !v3Available || !keywordId) return;
    const controller = new AbortController();
    setV3Loading(true);
    void getCreativeV3Preview(keywordId, controller.signal)
      .then(result => {
        if (!controller.signal.aborted) {
          setV3Preview(result);
          setV3Error("");
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted)
          setV3Error(reason instanceof Error ? reason.message : "V3 preview unavailable.");
      })
      .finally(() => { if (!controller.signal.aborted) setV3Loading(false); });
    return () => controller.abort();
  }, [active, advancedOpen, v3Available, keywordId]);

  useEffect(() => {
    setSavedVersions([]); setStage2Selected([]); setMvpMessage("");
    if (!active || !advancedOpen || !keywordId) return;
    let cancelled = false;
    void listSavedTypographyV2(keywordId).then(result => {
      if (!cancelled) setSavedVersions(result.items);
    }).catch(() => { /* The separate V2 publishing pilot may be disabled. */ });
    return () => { cancelled = true; };
  }, [active, advancedOpen, keywordId]);


  const visibleKeywords = useMemo(() => {
    const options = new Map(pickedKeywords.map(item => [item.keyword_id, item]));
    if (selectedKeyword) options.set(selectedKeyword.keyword_id, selectedKeyword);
    return [...options.values()];
  }, [pickedKeywords, selectedKeyword]);
  const currentKeyword = visibleKeywords.find(item => item.keyword_id === keywordId);
  const isPicked = pickedKeywords.some(item => item.keyword_id === keywordId);
  const applicableReferences = references.filter(item => item.scope !== "keyword" || item.keyword_id === keywordId);
  const reference = applicableReferences.find(item => item.id === referenceId);
  useEffect(() => {
    setReferenceId(previous => {
      const available = references.filter(item => item.scope !== "keyword" || item.keyword_id === keywordId);
      if (available.some(item => item.id === previous)) return previous;
      return available.find(item => item.scope === "keyword" && item.keyword_id === keywordId)?.id
        || available[0]?.id || "";
    });
  }, [keywordId, references]);
  const currentV3 = v3Preview?.concepts.find(item => item.source_mode === v3Mode);
  const v3ExactTextMatch = !v3Preview || Boolean(currentKeyword && v3Preview.keyword_exact === currentKeyword.keyword);

  const createPreviews = async (selected: StyleLabKeyword, source: TypographyReference) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true); setError(""); setPreviews(null);
    try {
      const result = await previewTypographyStyle({
        reference_id: source.id, keyword_id: selected.keyword_id, count: 3,
      }, controller.signal);
      if (!controller.signal.aborted) {
        const unsafe = !result.preview_only || result.job_created || result.provider_calls !== 0
          || result.keyword_id !== selected.keyword_id || result.reference_id !== source.id
          || result.source_keyword_exact !== selected.keyword
          || result.items.some(item => !item.preview_only || item.eligible_for_generation
            || item.source_keyword_exact !== selected.keyword);
        if (unsafe) {
          setError("Unsafe preview response. No concepts were displayed.");
        } else {
          setPreviews(result);
        }
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to create previews.");
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  // One bounded, no-cost preview per eligible keyword/reference pair.
  // The server revalidates tenant, rights, current accepted G3 DNA and picked state.
  useEffect(() => {
    if (!active || !autoPreview || loadingReferences || !hasLoadedOptions || !reference || !currentKeyword || !isPicked) return;
    const pair = currentKeyword.keyword_id + ":" + reference.id;
    if (autoAttemptRef.current === pair) return;
    autoAttemptRef.current = pair;
    void createPreviews(currentKeyword, reference);
  }, [active, autoPreview, loadingReferences, hasLoadedOptions, keywordId, referenceId, pickedKeywords, references]);

  const previewEnabled = Boolean(currentKeyword && isPicked && reference && !loadingReferences && !busy);
  const autoMessage = loadingReferences ? "Loading approved Style DNA and picked keywords…"
    : !hasLoadedOptions ? "Could not load Style Lab options."
      : !pickedKeywords.length ? "No picked keywords found. Pick a keyword in Stage 0 first."
        : !currentKeyword ? "Choose a picked keyword to start."
          : !isPicked ? "This keyword is not picked in Stage 0. Select an eligible keyword."
            : !reference ? approvedCount
              ? "Approved references need an accepted, current G3 analysis before auto preview."
              : "No approved reference yet. Review a typography idea first."
              : busy ? "Generating 3 SVG drafts automatically…"
                : previews?.items.length ? "3 SVG previews ready · No ImageGen · No job queued"
                  : error ? "Preview unavailable. Review the details and retry."
                    : autoPreview ? "Ready for automatic preview." : "Automatic preview is paused.";

  return <section className="typography-style-lab typography-style-lab-compact" aria-label="Typography Style Lab" data-testid="typography-style-lab">
    <header className="typography-style-lab-heading">
      <div>
        <p className="typography-eyebrow">STAGE 1 / STYLE LAB</p>
        <h3>Typography Auto Preview</h3>
        <p>Automatically compose 3 original SVG layouts from a picked keyword and approved Style DNA. No ImageGen or publishing.</p>
      </div>
      <button type="button" className="typography-quiet" onClick={onBack}>Back to Keyword Jobs</button>
    </header>

    <section className="typography-style-lab-auto" aria-label="Automatic typography preview settings">
      <div className="typography-style-lab-auto-controls">
        <label>Picked keyword
          <select aria-label="Picked keyword" value={keywordId} disabled={loadingReferences || !hasLoadedOptions}
            onChange={event => setKeywordId(event.target.value)}>
            <option value="">Select a picked keyword</option>
            {visibleKeywords.map(item => <option key={item.keyword_id} value={item.keyword_id}>
              {item.keyword}{!pickedKeywords.some(picked => picked.keyword_id === item.keyword_id) ? " · Not picked" : ""}
            </option>)}
          </select>
        </label>
        <label>Approved Style DNA
          <select aria-label="Style reference" value={referenceId} disabled={loadingReferences || !applicableReferences.length}
            onChange={event => setReferenceId(event.target.value)}>
            {!applicableReferences.length && <option value="">No accepted G3 references</option>}
            {applicableReferences.map(item => <option key={item.id} value={item.id}>{labelFor(item)}</option>)}
          </select>
        </label>
        <label className="typography-style-lab-auto-switch">
          <input type="checkbox" aria-label="Auto preview" checked={autoPreview}
            onChange={event => setAutoPreview(event.target.checked)} />
          <span>Auto preview<small>3 SVG drafts, no AI cost</small></span>
        </label>
        <button type="button" className="typography-primary typography-style-lab-refresh"
          disabled={!previewEnabled}
          onClick={() => { if (currentKeyword && reference) void createPreviews(currentKeyword, reference); }}>
          {busy ? "Generating…" : previews?.items.length ? "Regenerate previews" : "Preview 3 concepts"}
        </button>
      </div>
      <div className="typography-style-lab-auto-bottom">
        <p className="typography-style-lab-auto-message" role="status">{autoMessage}</p>
        {reference && <div className="typography-style-lab-auto-reference">
          <img src={reference.image_url} alt="" loading="lazy" />
          <span>Reference: <strong>{labelFor(reference)}</strong></span>
        </div>}
        {moreReferences && <small>Showing up to 100 highest-priority approved references.</small>}
      </div>
      {hasLoadedOptions && !pickedKeywords.length && <p className="typography-style-lab-hint">
        Pick a keyword in Stage 0 or open an eligible keyword from Keyword Jobs.</p>}
      {hasLoadedOptions && !references.length && <div className="typography-style-lab-next-step">
        <span>{approvedCount
          ? "References exist, but none has accepted current G3 DNA and verified rights."
          : "There are no approved typography references in this workspace."}</span>
        {onOpenIdeas && <button type="button" className="typography-quiet" onClick={onOpenIdeas}>Open Typography Ideas</button>}
      </div>}
      {error && <p role="alert" className="typography-style-lab-error">{error}</p>}
    </section>

    <section className="typography-style-lab-output" aria-label="Concept previews">
      <div className="typography-style-lab-output-heading">
        <div><p className="typography-style-lab-eyebrow">AUTO PREVIEW · NO IMAGEGEN</p>
          <h4>3 typography layouts</h4></div>
        <span>Vector drafts only · Human review before embroidery</span>
      </div>
      {previews?.items.length ? <div className="typography-style-lab-results" aria-live="polite">
        {previews.items.map((preview, index) => <article key={preview.id} className="typography-style-lab-preview">
          <div className="typography-style-lab-image">
            <img src={vectorSource(preview)} alt={"Preview " + (index + 1) + ": " + previews.source_keyword_exact} />
          </div>
          <div><strong>{index + 1}. {preview.layout.replaceAll("_", " ")}</strong>
            <small>{preview.font_family} · Exact text · Draft only</small></div>
        </article>)}
      </div> : <div className="typography-style-lab-empty" role="status">
        <strong>{busy ? "Generating layout previews…" : "No concepts yet"}</strong>
        <p>{loadingReferences ? "Loading eligible keywords and reference DNA…"
          : !isPicked ? "Select a picked keyword to begin."
            : !reference ? "Approve a typography reference and its G3 DNA in Typography Ideas."
              : autoPreview ? "The automatic SVG preview will appear here." : "Enable Auto preview or click Preview 3 concepts."}</p>
      </div>}
      {previews?.generation_plan?.style_brief && <details className="typography-style-lab-plan">
        <summary>Read generated style brief</summary>
        <p>{previews.generation_plan.style_brief}</p>
      </details>}
    </section>

    <details className="typography-style-lab-advanced" onToggle={event => setAdvancedOpen(event.currentTarget.open)}>
      <summary>Advanced tools <small>Creative V3 · Reviewed outputs · QA details</small></summary>
      <div className="typography-style-lab-advanced-body">
        <p className="typography-style-lab-qa-note">
          Preflight: exact keyword, tenant access, approved rights and human-accepted current G3 DNA are revalidated by the API.
          Previews do not generate images, save outputs or queue jobs. Embroidery QA requires human review.
        </p>
    {v3Available && <section className="typography-creative-v3" aria-label="Creative Engine V3 concept preview">
      <header className="typography-creative-v3-heading">
        <div><p className="typography-eyebrow">CREATIVE ENGINE V3 · P1 PREVIEW</p>
          <h4>Meaning-first concept directions</h4>
          <p>Connect an analyzed keyword to three creative routes, before choosing a style reference.</p></div>
        <span>Preview only · No ImageGen</span>
      </header>
      {v3Loading && <p role="status" className="typography-creative-v3-hint">Loading existing Creative Brief…</p>}
      {v3Error && <p role="alert" className="typography-creative-v3-warning">Creative V3 preview unavailable: {v3Error}</p>}
      {!v3Loading && !v3Error && (!v3Preview || v3Preview.planner_status === "v1_brief_not_ready") &&
        <p role="status" className="typography-creative-v3-hint">
          {keywordId ? "Analyze this keyword first in Keyword Jobs → Creative Brief. No generation is triggered here."
            : "Select a picked keyword to inspect its existing Creative Brief."}
        </p>}
      {v3Preview?.planner_status === "v1_bridge_preview_only" && !v3ExactTextMatch &&
        <p role="alert" className="typography-creative-v3-warning">Source keyword changed. Preview hidden; reload the original Creative Brief.</p>}
      {v3Preview?.planner_status === "v1_bridge_preview_only" && v3ExactTextMatch && <div className="typography-creative-v3-body">
        <div className="typography-creative-v3-routes" role="group" aria-label="Creative V3 concept routes">
          {v3Preview.concepts.map(item => <button key={item.id} type="button"
            aria-pressed={v3Mode === item.source_mode} onClick={() => setV3Mode(item.source_mode)}
            className={v3Mode === item.source_mode ? "active" : ""}>
            <small>{item.mode_label}</small><strong>{item.name}</strong>
            <span>{item.description}</span>
          </button>)}
        </div>
        {currentV3 && <div className="typography-creative-v3-detail">
          <strong>Creative hook</strong>
          <p>{currentV3.creative_hook}</p>
          <small>Mechanism: {currentV3.mechanism || "Needs classification"} ·
            Blueprint: not composed · QA: needs review</small>
          <div className="typography-creative-v3-locked">
            <strong>Exact text locked</strong>
            <span>{v3ExactTextMatch ? currentKeyword?.keyword : "Source text mismatch — preview blocked."}</span>
          </div>
          <p className="typography-creative-v3-hint">Derived from V1; not a completed V3 planner or approved artwork. Explore/Exploit requires explicit feedback and validation.</p>
        </div>}
      </div>}
    </section>}
        <div className="typography-v2-mvp" aria-label="Reviewed Typography V2 outputs">
          <h4>Reviewed outputs · MVP</h4>
          <p>Save existing human-approved ImageGen images. No images are generated here.</p>
          <label>Reviewed batch UUID <input aria-label="Reviewed batch UUID" value={batchId}
            onChange={event => setBatchId(event.target.value)} placeholder="UUID4 batch ID" /></label>
          <div className="typography-v2-mvp-slots" role="group" aria-label="Reviewed slots">
            {[1, 2, 3, 4, 5, 6].map(slot => <label key={slot}>
              <input type="checkbox" checked={chosenSlots.includes(slot)}
                onChange={event => setChosenSlots(previous => event.target.checked
                  ? [...previous, slot].sort((a, b) => a - b)
                  : previous.filter(item => item !== slot))} />{slot}</label>)}
          </div>
          <button type="button" className="typography-primary" onClick={() => void saveReviewed()}
            disabled={!keywordId || !referenceId || !batchId.trim() || !chosenSlots.length || saving}>
            {saving ? "Saving…" : "Save reviewed images"}
          </button>
          <button type="button" className="typography-quiet" disabled={!keywordId}
            onClick={() => void refreshHistory().catch(() => setError("Cannot load V2 history."))}>
            Refresh history
          </button>
          {savedVersions.map(item => <div key={item.version} className="typography-v2-mvp-version">
            <span>Version {item.version} · Slot {item.slot}</span>
            <button type="button" disabled={changing !== null} onClick={() => void setStage2(item.version)}>
              {stage2Selected.includes(item.version) ? "Remove from Stage 2" : "Select for Stage 2"}
            </button>
          </div>)}
          {mvpMessage && <p role="status">{mvpMessage}</p>}
          <small>Manual only · ImageGen/Scout not triggered · V1 untouched</small>
        </div>

      </div>
    </details>
  </section>;
}
