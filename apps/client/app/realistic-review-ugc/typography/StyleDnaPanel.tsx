import { useEffect, useState } from "react";
import { RrugcApiError } from "../api";
import { getTypographyAnalysisStatus, listTypographyDna, reviewTypographyDna, runTypographyAnalysis } from "./api";
import type { TypographyAnalysisState, TypographyDna, TypographyReference } from "./types";

const VERIFIED_RIGHTS = new Set(["owned", "licensed", "permitted_reference"]);
const REJECTION_REASONS = [
  ["region_uncertain", "Typography region uncertain"],
  ["font_mismatch", "Font mood incorrect"],
  ["layout_mismatch", "Layout incorrect"],
  ["overlap_unreadable", "Overlap not readable"],
  ["low_confidence", "Confidence too low"],
  ["near_duplicate", "Near-duplicate reference"],
  ["rights_concern", "Source rights concern"],
  ["copy_risk", "Copying risk"],
  ["no_typography", "No relevant typography"],
  ["other", "Other reason"],
] as const;

function errorText(error: unknown): string {
  if (error instanceof RrugcApiError) return error.code ? `${error.message} (${error.code})` : error.message;
  return error instanceof Error ? error.message : "Unable to update Style DNA.";
}

function summary(value: Record<string, unknown> | unknown[] | null | undefined): string {
  if (!value) return "Not specified";
  if (Array.isArray(value)) return value.length ? `${value.length} relationship${value.length === 1 ? "" : "s"}` : "No overlaps";
  const entries = Object.entries(value).filter(([, entry]) => typeof entry === "string" || typeof entry === "number");
  return entries.length ? entries.map(([key, entry]) => `${key.replace(/_/g, " ")}: ${String(entry)}`).join(" · ") : "See structure below";
}

export function StyleDnaPanel({ reference, canManage, onReviewed }: {
  reference: TypographyReference;
  canManage: boolean;
  onReviewed: (analysisStatus: TypographyReference["analysis_status"]) => void;
}) {
  const [dna, setDna] = useState<TypographyDna[]>([]);
  const [status, setStatus] = useState<TypographyAnalysisState | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState<string>("region_uncertain");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true); setError("");
    const load = async (first: boolean) => {
      try {
        const [results, analysis] = await Promise.all([
          listTypographyDna(reference.id, controller.signal),
          getTypographyAnalysisStatus(reference.id, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setDna(results.items); setStatus(analysis); setError("");
        if (analysis.job?.status === "queued" || analysis.job?.status === "running") {
          timer = window.setTimeout(() => void load(false), 4500);
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorText(cause));
      } finally {
        if (first && !controller.signal.aborted) setLoading(false);
      }
    };
    void load(true);
    return () => { controller.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [reference.id, revision]);

  const run = async () => {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await runTypographyAnalysis(reference.id);
      setStatus(result);
      setNotice(result.outcome === "cache_hit"
        ? "Existing Style DNA found. Review it below."
        : "Analysis requested. The job is tracked below.");
      setRevision(number => number + 1);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };
  const review = async (item: TypographyDna, decision: "accepted" | "needs_review" | "rejected") => {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await reviewTypographyDna(
        reference.id, item.id, decision,
        decision === "accepted" ? (item.confidence < .65 ? "low_confidence_reviewed" : "approved") : reason,
      );
      setDna(current => current.map(dna => dna.id === item.id ? { ...dna, review: result.review } : dna));
      setNotice("DNA review saved: " + decision.replace("_", " ") + ".");
      onReviewed(decision === "accepted" ? "ready" : "needs_review");
      setRevision(number => number + 1);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };
  const approved = reference.editorial_status === "approved" && VERIFIED_RIGHTS.has(reference.source_rights_status);
  const running = status?.job?.status === "running";
  const queued = status?.job?.status === "queued";
  const canAnalyze = Boolean(canManage && status?.can_request_analysis && !busy && !queued && !running && !dna.length);
  const blocker = status?.analysis_blocker === "rights_unverified"
    ? "Source rights are unverified. Edit metadata and provide a valid rights basis before running G3."
    : status?.analysis_blocker === "worker_disabled"
    ? "G3 processing is not enabled for this tenant. No AI request has been started."
    : status?.analysis_blocker === "reference_inactive"
    ? "This reference is inactive and cannot be analyzed."
    : "";

  return <div className="typography-dna-panel" aria-live="polite">
    {loading ? <p role="status">Checking analyzer and Style DNA…</p> : <>
      <div className="typography-dna-status">
        <div><strong>G3 Visual Analyzer</strong>
          <span>{running ? "Analyzing…" : queued ? "Queued" : status?.job?.status === "completed" ? "Completed" :
            status?.job?.status === "failed" || status?.job?.status === "needs_review" ? "Needs attention" :
            dna.length ? "Analysis available" : "Not started"}</span>
        </div>
        {canAnalyze && <button type="button" className="typography-primary" disabled={busy} onClick={() => void run()}>
          {busy ? "Starting…" : "Analyze Style DNA"}
        </button>}
      </div>
      {blocker && <p className="typography-dna-hint">{blocker}</p>}
      {canAnalyze && <p className="typography-dna-hint">Manual request only · may incur a vision-provider charge · no bulk processing</p>}
      {(queued || running) && <p className="typography-dna-hint" role="status">
        {running ? "Processing image and typography geometry" : "Queued for the analyzer"} · updates automatically
      </p>}
      {status?.job?.last_error_code && <p className="typography-dna-hint is-error">
        Last analysis error: {status.job.last_error_code.replace(/_/g, " ")} · attempts: {status.job.attempt_count}
      </p>}
      {!dna.length && !queued && !running && <div className="typography-dna-empty">
        <strong>No analysis yet</strong>
        <p>{blocker || "Run the analyzer after verifying the source. No analysis starts just by opening this drawer."}</p>
      </div>}
      {dna.map(result => <article className="typography-dna-result" key={result.id}>
        <header><h4>{result.style_family}</h4><strong>{Math.round(result.confidence * 100)}% confidence</strong></header>
        <dl>
          <div><dt>Typography area</dt><dd>{summary(result.typography_region)}</dd></div>
          <div><dt>Font mood</dt><dd>{summary(result.font_mood)}</dd></div>
          <div><dt>Layout</dt><dd>
            {Array.isArray(result.layout_graph.nodes) ? result.layout_graph.nodes.length : 0} hierarchy nodes ·
            {" "}{Array.isArray(result.layout_graph.edges) ? result.layout_graph.edges.length : 0} relationships
          </dd></div>
          <div><dt>Overlap</dt><dd>{summary(result.overlap_rules)}</dd></div>
        </dl>
        <div className="typography-dna-review">
          <span>Human review: <b>{result.review?.decision || "pending"}</b></span>
          {canManage && <div className="typography-dna-review-actions">
            <button type="button" disabled={!approved || busy || result.review?.decision === "accepted"}
              title={approved ? "Accept the reviewed structural DNA" : "Approve reference and verify rights first"}
              onClick={() => void review(result, "accepted")}>Accept DNA</button>
            <select aria-label="Style DNA review reason" value={reason} disabled={busy}
              onChange={event => setReason(event.target.value)}>
              {REJECTION_REASONS.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
            </select>
            <button type="button" disabled={busy} onClick={() => void review(result, "needs_review")}>Needs correction</button>
            <button type="button" disabled={busy} onClick={() => void review(result, "rejected")}>Reject DNA</button>
          </div>}
        </div>
      </article>)}
    </>}
    {notice && <p role="status" className="typography-dna-hint">{notice}</p>}
    {error && <div role="alert" className="typography-error">{error}<button type="button" onClick={() => setRevision(number => number + 1)}>Retry</button></div>}
  </div>;
}
