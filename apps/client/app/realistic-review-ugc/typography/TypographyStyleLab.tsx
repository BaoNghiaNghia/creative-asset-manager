import { useEffect, useMemo, useRef, useState } from "react";
import { listTypographyReferences, previewTypographyStyle, saveReviewedTypographyBatch, listSavedTypographyV2, handoffTypographyV2 } from "./api";
import type { TypographySavedVersion } from "./api";
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
  active, keywords, selectedKeyword, onBack,
}: {
  active: boolean;
  keywords: StyleLabKeyword[];
  selectedKeyword: StyleLabKeyword | null;
  onBack: () => void;
}) {
  const [keywordId, setKeywordId] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [references, setReferences] = useState<TypographyReference[]>([]);
  const [moreReferences, setMoreReferences] = useState(false);
  const [loadingReferences, setLoadingReferences] = useState(false);
  const [previews, setPreviews] = useState<TypographyPreviewResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

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
    void listTypographyReferences({ status: "approved", limit: 100 }, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        const eligible = page.items.filter(ref =>
          ref.editorial_status === "approved" &&
          (ref.source_rights_status === "owned" || ref.source_rights_status === "licensed" ||
            ref.source_rights_status === "permitted_reference"));
        setReferences(eligible);
        setMoreReferences(page.total > page.items.length);
        setReferenceId(previous => eligible.some(ref => ref.id === previous)
          ? previous : eligible[0]?.id || "");
      })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Reference lookup failed.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingReferences(false); });
    return () => controller.abort();
  }, [active]);

  useEffect(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy(false);
    setPreviews(null);
    setError("");
  }, [keywordId, referenceId]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  useEffect(() => {
    setSavedVersions([]); setStage2Selected([]); setMvpMessage("");
    if (!active || !keywordId) return;
    let cancelled = false;
    void listSavedTypographyV2(keywordId).then(result => {
      if (!cancelled) setSavedVersions(result.items);
    }).catch(() => { /* The separate V2 publishing pilot may be disabled. */ });
    return () => { cancelled = true; };
  }, [active, keywordId]);


  const visibleKeywords = useMemo(() => {
    const options = new Map(keywords.map(item => [item.keyword_id, item]));
    if (selectedKeyword) options.set(selectedKeyword.keyword_id, selectedKeyword);
    return [...options.values()];
  }, [keywords, selectedKeyword]);
  const currentKeyword = visibleKeywords.find(item => item.keyword_id === keywordId);
  const reference = references.find(item => item.id === referenceId);

  const createPreviews = async () => {
    if (!currentKeyword || !reference || busy) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true); setError(""); setPreviews(null);
    try {
      const result = await previewTypographyStyle({
        reference_id: reference.id, keyword_id: currentKeyword.keyword_id, count: 3,
      }, controller.signal);
      if (!controller.signal.aborted) {
        if (!result.preview_only || result.job_created || result.items.some(item => !item.preview_only || item.eligible_for_generation)) {
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

  return <section className="typography-style-lab" aria-label="Typography Style Lab" data-testid="typography-style-lab">
    <div className="typography-style-lab-heading">
      <div><p className="typography-eyebrow">STAGE 1 / STYLE LAB</p>
        <h3>Balanced Typography · Preview</h3>
        <p>Use approved Layout, Font and Overlap style DNA to explore original compositions without copying a reference.</p>
      </div>
      <button type="button" className="typography-quiet" onClick={onBack}>Back to Keyword Jobs</button>
    </div>
    <div className="typography-v2-mvp-guide">
      <strong>Typography V2 · Reviewed output workflow</strong>
      <span>01 · Preview style</span><span>02 · Human-review an existing ImageGen batch</span><span>03 · Save and select for Stage 2</span>
      <small>Image generation, publication and Stage 2 job creation are independently gated.</small>
    </div>
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
    <div className="typography-style-lab-grid">
      <section className="typography-style-lab-panel" aria-label="Reference selection">
        <p className="typography-style-lab-eyebrow">01 / REFERENCES</p>
        <h4>Pick an approved style</h4>
        <label>Reference image<select aria-label="Style reference" disabled={loadingReferences || !references.length}
          value={referenceId} onChange={event => setReferenceId(event.target.value)}>
          {!references.length && <option value="">No approved references</option>}
          {references.map(ref => <option key={ref.id} value={ref.id}>{labelFor(ref)}</option>)}
        </select></label>
        {reference && <div className="typography-style-lab-reference">
          <img src={reference.image_url} alt={labelFor(reference)} loading="lazy" />
          <strong>{labelFor(reference)}</strong>
          <span>Rights: {reference.source_rights_status} · {reference.editorial_status}</span>
          <small>Source artwork is analyzed for relationships, not copied.</small>
        </div>}
        {moreReferences && <p className="typography-style-lab-warning">Showing the first 100 approved references. Filter or narrow your library to use others.</p>}
        {!loadingReferences && !references.length && <p className="typography-style-lab-hint">
          Upload a reference in Typography Ideas, verify its rights, run G3 analysis and accept Style DNA before previewing.</p>}
      </section>
      <section className="typography-style-lab-panel typography-style-lab-previews" aria-label="Concept previews">
        <p className="typography-style-lab-eyebrow">02 / CONCEPT PREVIEWS</p>
        <h4>Three structural variations</h4>
        {previews?.items.length ? <div className="typography-style-lab-results" aria-live="polite">
          {previews.items.map((preview, index) => <article key={preview.id} className="typography-style-lab-preview">
            <div className="typography-style-lab-image"><img
              src={vectorSource(preview)} alt={`Preview ${index + 1}: ${previews.source_keyword_exact}`} /></div>
            <div><strong>{index + 1}. {preview.layout.replaceAll("_", " ")}</strong>
              <small>{preview.font_family} · Exact text · Draft only</small></div>
          </article>)}
        </div> : <div className="typography-style-lab-empty" role="status">
          <strong>No concepts yet</strong>
          <p>Choose one picked keyword and an approved reference, then create three no-cost SVG previews.</p>
        </div>}
        <p className="typography-style-lab-notice">Vector drafts only. This does not create an AI image, save an output or publish a product.</p>
      </section>
      <aside className="typography-style-lab-panel" aria-label="Balanced controls and quality">
        <p className="typography-style-lab-eyebrow">03 / CONTROLS & QA</p>
        <h4>Keyword & constraints</h4>
        <label>Picked keyword <select aria-label="Picked keyword" value={keywordId}
          onChange={event => setKeywordId(event.target.value)}>
          <option value="">Select a keyword</option>
          {visibleKeywords.map(item => <option key={item.keyword_id} value={item.keyword_id}>{item.keyword}</option>)}
        </select></label>
        {!visibleKeywords.length && <p className="typography-style-lab-hint">No keyword loaded. Return to Keyword Jobs and click Apply style on a row.</p>}
        {currentKeyword && <div className="typography-style-lab-exact"><small>Original keyword · locked</small>
          <strong>{currentKeyword.keyword}</strong></div>}
        <p className="typography-style-lab-controls-title">Balanced mode <span>Locked for pilot</span></p>
        <div className="typography-style-lab-components">
          {["Layout", "Font", "Overlap"].map(name => <label key={name}>
            <input type="checkbox" checked disabled readOnly />{name}</label>)}
        </div>
        <div className="typography-style-lab-qa">
          <strong>Preflight QA</strong>
          <p>Exact keyword lock, rights-approved DNA, tenant isolation and read-only previews are enforced by the API.</p>
          <p>Origin/density and embroidery quality still require human review before Stage 1 generation.</p>
        </div>
        {error && <p role="alert" className="typography-style-lab-error">{error}</p>}
        <button type="button" className="typography-primary" disabled={!currentKeyword || !reference || busy}
          onClick={() => void createPreviews()}>{busy ? "Creating previews…" : "Preview 3 concepts"}</button>

        <small className="typography-style-lab-status">0 ImageGen calls · 0 jobs queued · V1 unchanged</small>

      </aside>
    </div>
  </section>;
}
