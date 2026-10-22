import { useEffect, useId, useState } from "react";
import { getEcommerceInputReadiness, getEcommerceJobLogs, getEcommerceOutputVersions, regenerateEcommerceJob, retryEcommerceJob, runEcommerceJob, type EcommerceInputReadiness, type EcommerceJob, type EcommerceJobLogs, type EcommerceOutputVersion, type ColorwayJob, type ProductsStage2PhotoReadiness } from "./api";
import "./ProductsPhotoWorkflow.css";
import { EcommerceInputGalleryModal, type InputPreviewItem } from "./EcommerceInputGalleryModal";
import type { ProductStage2SkillKey } from "./workflowSkillSlots";

type StepDefinition = {
  key: ProductStage2SkillKey;
  readinessKey: "ecommerce" | "multihat" | "ugc";
  title: string;
  skillName: string;
  shots: ReadonlyArray<{ label: string; number: number }>;
  inputs?: ReadonlyArray<{ label: string; kind: "design" | "front" | "embroidered_front" | "angle" | "side" | "back" }>;
  inputInstruction?: string;
};

/** Explicit 1-to-1 Skill → Ecommerce photo mapping; ten existing photos total. */
const PHOTO_STEPS: StepDefinition[] = [
  { key: "prod_s2_ec1", readinessKey: "ecommerce", title: "Ecommerce · Image 01", skillName: "Not assigned", shots: [{ label: "Front High", number: 1 }], inputs: [{ label: "Embroidery artwork", kind: "design" }, { label: "Front stock", kind: "front" }], inputInstruction: "Hat Front Embroidery: preserve the original design within the calibrated front embroidery area." },
  { key: "prod_s2_ec2", readinessKey: "ecommerce", title: "Ecommerce · Image 02", skillName: "Not assigned", shots: [{ label: "Front Right 3/4", number: 2 }], inputs: [{ label: "Embroidery artwork", kind: "design" }, { label: "Front embroidered reference", kind: "embroidered_front" }, { label: "Front stock for perspective", kind: "front" }, { label: "Official right-side geometry", kind: "side" }], inputInstruction: "Hat ThreeQuarter Embroidery: match the front design and cap color while adjusting placement to the angled crown." },
  { key: "prod_s2_ec3", readinessKey: "ecommerce", title: "Ecommerce · Image 03", skillName: "Not assigned", shots: [{ label: "Right Side", number: 3 }], inputs: [{ label: "Embroidery artwork", kind: "design" }, { label: "Front embroidered reference", kind: "embroidered_front" }, { label: "Right-side stock", kind: "side" }], inputInstruction: "Hat Side90 Embroidery: preserve the design, scaled for the visible side perspective, without inventing lettering." },
  { key: "prod_s2_ec4", readinessKey: "ecommerce", title: "Ecommerce · Image 04", skillName: "Not assigned", shots: [{ label: "Back High", number: 4 }], inputs: [{ label: "Back stock reference", kind: "back" }], inputInstruction: "GatorHats 8869 Front to Back: use the real back stock image; do not invent rear embroidery." },
  { key: "prod_s2_mh", readinessKey: "multihat", title: "Multi-hat", skillName: "gatorhats-8869-multihat-v2-4", shots: [
    { label: "Multi-hat 1", number: 5 }, { label: "Multi-hat 2", number: 6 }, { label: "Multi-hat 3", number: 7 },
  ] },
  { key: "prod_s2_ugc", readinessKey: "ugc", title: "UGC", skillName: "gatorhats-8869-ugc-v3", shots: [
    { label: "UGC 1", number: 8 }, { label: "UGC 2", number: 9 }, { label: "UGC 3", number: 10 },
  ] },
];

export const PHOTO_COLOR_KEYS = [
  "khaki-maroon", "natural-black", "natural-brown", "natural-camo-green",
  "natural-charcoal", "natural-forest-green", "natural-khaki", "natural-maroon",
  "natural-mossy-oak-breakup", "natural-navy", "natural-realtree-all-purpose",
  "natural-red", "natural-royal",
] as const;

export function legacyMissingAssetsForStep(isEcommerce: boolean, missingAssets: string[] | undefined): string[] {
  return isEcommerce ? [] : missingAssets || [];
}

function openSkillsManager() {
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>(".rrugc-global-management-button"))
    .find(item => item.textContent?.includes("Manage skills"));
  button?.click();
}

function HatSlotIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M5.2 13.1c.5-4.5 3.2-7.2 6.8-7.2s6.3 2.7 6.8 7.2" />
    <path d="M4.1 13.1h14.7c1.4 0 2.4.5 2.8 1.4-2.4 1.7-5.6 2.5-9.6 2.5-4 0-7.2-.8-9.6-2.5.3-.9.9-1.4 1.7-1.4Z" />
    <path d="M12 6v7" />
  </svg>;
}

export type ColorwaySkillOption = {
  value: string;
  label: string;
  ready: boolean;
};

type Props = {
  sourceName: string;
  lastUpdated?: string | null;
  sourcePlanId?: string;
  designSrc?: string;
  ecommerceJobs?: EcommerceJob[];
  onEcommerceJobChange?: () => void | Promise<void>;
  readiness: ProductsStage2PhotoReadiness | null;
  colors?: ColorwayJob[];
  colorwaySkillName?: string;
  colorwaySkillKey?: string;
  colorwaySkillOptions?: ColorwaySkillOption[];
  colorwaySkillDisabled?: boolean;
  onColorwaySkillChange?: (value: string) => void;
  colorwayReady?: boolean;
  colorwayRunning?: boolean;
  onQueueColors?: () => void;
  onRetryColor?: (job: ColorwayJob) => void;
  onOpenColor?: (job: ColorwayJob) => void;
  onOpenLog?: (job: ColorwayJob) => void;
  configuredSkillNames?: Partial<Record<ProductStage2SkillKey, string>>;
};

export function ProductsPhotoWorkflow({
  sourceName, lastUpdated, sourcePlanId = "", designSrc = "", ecommerceJobs: jobs = [], onEcommerceJobChange, readiness, colors = [], colorwaySkillName, colorwaySkillKey = "",
  colorwaySkillOptions = [], colorwaySkillDisabled = false, onColorwaySkillChange,
  colorwayReady = false, colorwayRunning = false,
  onQueueColors, onRetryColor, onOpenColor, onOpenLog, configuredSkillNames = {},
}: Props) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const [previewGallery, setPreviewGallery] = useState<InputPreviewItem[] | null>(null);
  const [inputs, setInputs] = useState<EcommerceInputReadiness | null>(null);
  const [busyStage, setBusyStage] = useState<string | null>(null);
  const [jobActionError, setJobActionError] = useState("");
  const [logFor, setLogFor] = useState<string | null>(null);
  const [logs, setLogs] = useState<EcommerceJobLogs | null>(null);
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [versions, setVersions] = useState<EcommerceOutputVersion[]>([]);
  useEffect(() => {
    if (!sourcePlanId || !expandedStep?.startsWith("prod_s2_ec")) return;
    const controller = new AbortController();
    const refresh = () => void getEcommerceInputReadiness(sourcePlanId, controller.signal)
      .then(result => { if (!controller.signal.aborted) setInputs(result); })
      .catch(() => { if (!controller.signal.aborted) setInputs(null); });
    refresh();
    const ticker = window.setInterval(refresh, 8000);
    return () => { controller.abort(); window.clearInterval(ticker); };
  }, [sourcePlanId, expandedStep]);

  async function invokeEcommerce(stage: string, job?: EcommerceJob, regenerate = false) {
    if (!sourcePlanId || busyStage) return;
    setBusyStage(stage);
    setJobActionError("");
    try {
      if (job && regenerate) await regenerateEcommerceJob(job.id);
      else if (job && job.status === "failed") await retryEcommerceJob(job.id);
      else await runEcommerceJob(sourcePlanId, stage);
      await onEcommerceJobChange?.();
      setInputs(await getEcommerceInputReadiness(sourcePlanId));
    } catch (reason) {
      setJobActionError(reason instanceof Error ? reason.message : "Unable to run Ecommerce Skill.");
      setExpandedStep(stage);
    } finally {
      setBusyStage(null);
    }
  }

  async function showEcommerceLogs(jobId: string) {
    setLogFor(jobId);
    setVersionsFor(null);
    setLogs(null);
    try { setLogs(await getEcommerceJobLogs(jobId)); }
    catch (reason) { setJobActionError(reason instanceof Error ? reason.message : "Cannot load logs"); }
  }

  async function showEcommerceVersions(jobId: string) {
    setVersionsFor(jobId);
    setLogFor(null);
    setVersions([]);
    try { setVersions(await getEcommerceOutputVersions(jobId)); }
    catch (reason) { setJobActionError(reason instanceof Error ? reason.message : "Cannot load versions"); }
  }

  const colorSkillSelectId = useId();
  const byColor = new Map(colors.map(job => [job.color_key, job]));
  const complete = colors.filter(job => job.status === "completed").length;
  const failed = colors.filter(job => job.status === "failed").length;
  const active = colors.filter(job => job.status === "running" || job.status === "queued").length;
  const colorStatus = failed ? failed + " failed" : active ? active + " active" : complete === 13 ? "Completed" : complete > 0 ? complete + "/13 done" : "Not run";

  return <div className="rrugc-products-photo-pipeline" aria-label={"Seven-skill photo workflow for " + sourceName}>
    <div className="rrugc-products-photo-pipeline-summary">
      <strong>7 skill slots <span aria-hidden="true">·</span> 23 outputs</strong>
      <small>Skills 1–4: Ecommerce Images 01–04 · Skill 5: Multi-hat · Skill 6: UGC · Skill 7: 13 Colors</small>
      <a className="rrugc-products-photo-download-all" href={sourcePlanId ? `/api/v1/realistic-review-ugc/products/source-plans/${encodeURIComponent(sourcePlanId)}/outputs/archive` : undefined} aria-disabled={!sourcePlanId || (!jobs.some(job => job.status === "completed" && job.output_url) && !colors.some(job => job.status === "completed" && job.output_url))} onClick={event => { if (!sourcePlanId || (!jobs.some(job => job.status === "completed" && job.output_url) && !colors.some(job => job.status === "completed" && job.output_url))) event.preventDefault(); }} title="Download all completed images from the 7 skill slots as ZIP">Download all outputs ↓</a>
    </div>
    <div className="rrugc-products-photo-stage-list">
      <div className="rrugc-products-photo-group-label">Ecommerce · 4 individual image Skills</div>
      {PHOTO_STEPS.map((step, index) => {
        const state = readiness?.steps.find(item => item.key === step.readinessKey);
        const opened = expandedStep === step.key;
        const configured = configuredSkillNames[step.key];
        const skillName = configured || step.skillName;
        const isEcommerce = index < 4;
        const slotInputs = inputs?.slots.find(item => item.stage === step.key);
        const previewItems: InputPreviewItem[] = (step.inputs || []).map(input => ({
          key: input.kind,
          label: input.label,
          src: input.kind === "design" ? designSrc : input.kind === "embroidered_front"
            ? (jobs.find(job => job.stage === "prod_s2_ec1")?.output_url || "")
            : input.kind === "front" ? "/rrugc/blueprint/fronts/natural-black.webp"
            : input.kind === "back" ? "/rrugc/blueprint/stock/natural-black/back.jpg"
            : "/rrugc/blueprint/stock/natural-black/side.jpg",
        })).filter(item => Boolean(item.src));
        const shotJob = jobs.find(item => item.stage === step.key);
        const runnable = isEcommerce && Boolean(configured && slotInputs?.ready && sourcePlanId)
          && !busyStage && !shotJob;

        // Legacy aggregate Ecommerce readiness never makes an individual shot runnable.
        const status = shotJob ? shotJob.status.replace(/^./, char => char.toUpperCase())
          : !configured ? "Needs skill" : isEcommerce
          ? (slotInputs?.ready ? "Ready" : slotInputs?.status === "missing_inputs"
            ? "Missing input" : slotInputs?.status === "worker_pending"
              ? "Worker pending" : "Needs skill")
          : state?.ready ? "Ready" : "Worker pending";
        return <div key={step.key} className="rrugc-products-photo-stage-row" data-skill={step.key}>
          <div className="rrugc-products-photo-stage-title">
            <span className="rrugc-products-photo-number">{index + 1}</span>
            <div><strong>{step.title}</strong><small>{step.shots.length} {step.shots.length === 1 ? "shot" : "shots"}</small></div>
          </div>
          <div className="rrugc-products-photo-output-strip" role="group" aria-label={step.title + " output slots"}>
            {step.shots.map(shot => shotJob?.output_url && shotJob.status === "completed"
              ? <button type="button" key={shot.number} className="rrugc-products-photo-empty-shot is-completed"
                  onClick={() => setPreviewGallery([{ key: shotJob.id, label: shot.label + " · output", src: shotJob.output_url! }])}
                  aria-label={"Open " + shot.label + " Ecommerce output"}>
                  <img src={shotJob.output_url} alt={shot.label} loading="lazy" />
                </button>
              : <span key={shot.number} className="rrugc-products-photo-empty-shot"
                  title={shot.label + " · " + (shotJob?.status || "Not generated")}
                  aria-label={shot.label + " · " + (shotJob?.status || "Not generated")}>
                  <HatSlotIcon />
                </span>)}
          </div>
          <div className="rrugc-products-photo-skill" title={skillName}>
            <span>Skill {index + 1}</span><strong>{skillName}</strong>
          </div>
          <div className="rrugc-products-photo-stage-controls">
            <time className="rrugc-products-photo-updated" title={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleString() : "No update date"} dateTime={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toISOString() : undefined}>Last updated: {lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleDateString() : "—"}</time>
            <span className={"rrugc-products-photo-state " + (status === "Ready" || status === "Completed" ? "is-done" : status === "Failed" ? "is-failed" : status === "Running" || status === "Queued" ? "is-running" : "is-unavailable")}
              title={isEcommerce
                ? (slotInputs ? "Missing persisted inputs: " + slotInputs.missing_roles.join(", ") : "Waiting for input readiness from server")
                : state?.required_inputs || "Photo generation worker is not enabled"}>{status}</span>
            <button type="button" className="rrugc-products-photo-run-button"
              aria-label={"Run " + step.title}
              aria-disabled={!runnable && !(shotJob?.status === "failed" && shotJob.retry_count < 3)}
              disabled={!!busyStage || Boolean(shotJob && (shotJob.status !== "failed" || shotJob.retry_count >= 3))}
              title={runnable ? "Queue Ecommerce image generation" : "Open details for prerequisites and job status"}
              onClick={() => runnable ? void invokeEcommerce(step.key)
                : shotJob?.status === "failed" ? void invokeEcommerce(step.key, shotJob)
                : setExpandedStep(step.key)}>{busyStage === step.key ? "Queueing…" : shotJob?.status === "failed" ? "Retry" : "Run"}</button>
            <button type="button" className="rrugc-products-photo-detail-toggle"
              aria-expanded={opened} aria-label={step.title + " details"}
              onClick={() => setExpandedStep(opened ? null : step.key)}>{opened ? "Hide" : "Details"} <span aria-hidden="true">{opened ? "⌃" : "⌄"}</span></button>
          </div>
          {opened && <div className="rrugc-products-photo-stage-detail">
            {isEcommerce && <div className="rrugc-products-photo-inputs" aria-label={step.title + " input references"}>
              <strong>Input references · {step.inputs?.length}</strong>
              <small>{slotInputs
                ? slotInputs.missing_roles.length
                  ? "Missing saved inputs: " + slotInputs.missing_roles.join(", ")
                  : "Saved inputs detected; worker and assigned skill must still be verified"
                : "Checking durable inputs on the server…"}
              </small>
              <div className="rrugc-products-photo-input-grid">
                {step.inputs?.map(input => {
                  const item = previewItems.find(preview => preview.key === input.kind);
                  return <div className="rrugc-products-photo-input" key={input.kind}>
                    {item ? <button type="button" className="rrugc-products-photo-preview-trigger" aria-label={"Preview " + input.label} onClick={() => setPreviewGallery(previewItems)}>
                      <img src={item.src} alt={input.label} loading="lazy" />
                      <span className="rrugc-products-photo-hover-preview" aria-hidden="true"><img src={item.src} alt="" /></span>
                    </button> : <span>Missing artwork</span>}
                    <small>{input.label}</small>
                  </div>;
                })}
              </div>
              <p>{step.inputInstruction}</p>
              <small>Actual inputs are checked by the server. Stock references are pinned to the released file hashes; generated Front references must complete before Skills 2–3 can run.</small>
            </div>}
            <div><strong>{skillName}</strong>
              <p>{isEcommerce
                ? "This Skill generates one independent Ecommerce photo and stores it in managed storage with job logs and output versions."
                : state?.required_inputs || "Requires verified stock and reference photography. Execution has not been enabled."}</p>
              {legacyMissingAssetsForStep(isEcommerce, state?.missing_assets).length > 0 && <small>Missing assets: {legacyMissingAssetsForStep(isEcommerce, state?.missing_assets).join(", ")}</small>}
            </div>
            <button type="button" onClick={openSkillsManager}>Manage skill</button>
            {shotJob && <button type="button" disabled={shotJob.status !== "failed" || !!busyStage || shotJob.retry_count >= 3}
              onClick={() => void invokeEcommerce(step.key, shotJob)}>Retry failed</button>}
            {shotJob && <button type="button" onClick={() => void showEcommerceLogs(shotJob.id)}>Logs</button>}
            {shotJob?.output_url && <button type="button" onClick={() => void showEcommerceVersions(shotJob.id)}>Versions</button>}
            {shotJob?.status === "completed" && <button type="button" disabled={!!busyStage}
              onClick={() => void invokeEcommerce(step.key, shotJob, true)}>Regenerate</button>}
            {!shotJob && <small>Logs available after the first job is queued</small>}
            {jobActionError && expandedStep === step.key && <p role="alert">{jobActionError}</p>}
            {shotJob && logFor === shotJob.id && logs && <div className="rrugc-products-photo-execution-log">
              <strong>Execution attempts</strong>
              {logs.attempts.map(attempt => <small key={attempt.id}>
                {attempt.status} · {attempt.attempt_count} attempt(s)
                {attempt.error_code ? " · " + attempt.error_code : ""}
                {attempt.error_message ? " · " + attempt.error_message : ""}
              </small>)}
            </div>}
            {shotJob && versionsFor === shotJob.id && <div className="rrugc-products-photo-execution-log">
              <strong>Saved output versions</strong>
              {versions.map(version => <a key={version.version} href={version.output_url}
                target="_blank" rel="noopener noreferrer">Version {version.version}</a>)}
            </div>}
          </div>}
        </div>;
      })}
      <div className="rrugc-products-photo-stage-row is-colorway" data-skill="colorway">
        <div className="rrugc-products-photo-stage-title">
          <span className="rrugc-products-photo-number">7</span>
          <div><strong>13 Hat Colors</strong><small>{complete}/13 images</small></div>
        </div>
        <div className="rrugc-products-photo-output-strip is-colors" role="group" aria-label="13 hat color output slots">
          {PHOTO_COLOR_KEYS.map((key, index) => {
            const job = byColor.get(key);
            const colorName = job?.color_name || key.replaceAll("-", " ");
            const status = job?.status || "pending";
            const preview = job?.output_url && job.status === "completed"
              ? <img loading="lazy" src={job.output_url} alt={colorName} />
              : <HatSlotIcon />;
            const content = preview;
            const className = "rrugc-colorway-slot is-" + status;
            if (job?.status === "completed" && job.output_url && onOpenColor) {
              return <button type="button" key={key} className={className} onClick={() => onOpenColor(job)}
                aria-label={"View " + colorName + " output versions"} title={colorName + " · View versions"}>{content}</button>;
            }
            if (job?.status === "failed" && onRetryColor) {
              return <button type="button" key={key} className={className}
                onClick={() => onRetryColor(job)} disabled={colorwayRunning || job.retry_count >= 3}
                aria-label={"Retry " + colorName}
                title={colorName + (job.retry_count >= 3 ? " · Retry limit reached" : " · Retry failed output")}>{content}</button>;
            }
            return <span key={key} className={className} title={colorName + " · " + (job?.status || "Not run")}
              aria-label={colorName + " · " + (job?.status || "Not run")}>{content}</span>;
          })}
        </div>
        <div className="rrugc-products-photo-skill" title={colorwaySkillName || "Not assigned"}>
          <span>Skill 7</span><strong>{colorwaySkillName || "Not assigned"}</strong>
        </div>
        <div className="rrugc-products-photo-stage-controls">
          <time className="rrugc-products-photo-updated" title={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleString() : "No update date"} dateTime={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toISOString() : undefined}>Last updated: {lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleDateString() : "—"}</time>
          <span className={"rrugc-products-photo-state " + (failed ? "is-failed" : active ? "is-running" : complete === 13 ? "is-done" : "")}>
            {colorStatus}
          </span>
          <button type="button" className="rrugc-products-photo-run-button" aria-label="Run 13 Hat Colors" disabled={!colorwayReady || colorwayRunning || complete === 13} onClick={onQueueColors}>{colorwayRunning ? "Queueing…" : "Run"}</button>
          <button type="button" className="rrugc-products-photo-detail-toggle"
            aria-expanded={expandedStep === "colorway"} aria-label="13 Hat Colors details"
            onClick={() => setExpandedStep(value => value === "colorway" ? null : "colorway")}>
            {expandedStep === "colorway" ? "Hide" : "Details"} <span aria-hidden="true">{expandedStep === "colorway" ? "⌃" : "⌄"}</span>
          </button>
        </div>
        {expandedStep === "colorway" && <div className="rrugc-products-photo-stage-detail">
          <div className="rrugc-products-photo-colorway-selector">
            <label htmlFor={colorSkillSelectId}>Skill 7 configuration</label>
            <select id={colorSkillSelectId} className="rrugc-products-photo-skill-select"
              aria-label={"13 Hat Colors skill for " + sourceName}
              title={colorwaySkillName || "Choose 13 Hat Colors skill"}
              value={colorwaySkillKey} disabled={colorwaySkillDisabled || !onColorwaySkillChange}
              onChange={event => onColorwaySkillChange?.(event.target.value)}>
              {colorwaySkillOptions.length === 0 && <option value="">{colorwaySkillDisabled ? "Loading skills…" : "No skill available"}</option>}
              {colorwaySkillOptions.map(option => <option key={option.value} value={option.value}>{option.label}{option.ready ? "" : " · needs sync"}</option>)}
            </select>
          </div>
          <div><strong>{colorwaySkillName || "Select colorway skill"}</strong>
            <p>Generate each of 13 Valucap 8869 colors. Finished versions stay available; click an image to view versions or a failed slot to retry.</p></div>
          <button type="button" onClick={openSkillsManager}>Manage skill</button>
          <button type="button" disabled={!colorwayReady || colorwayRunning || complete === 13}
            onClick={onQueueColors}>{colorwayRunning ? "Queueing…" : "Run missing"}</button>
          {!!colors.length && onOpenLog && <select aria-label={"Hat color skill logs for " + sourceName} value=""
            onChange={event => { const job = colors.find(item => item.id === event.target.value); if (job) onOpenLog(job); }}>
            <option value="">View logs…</option>
            {colors.map(job => <option key={job.id} value={job.id}>{job.color_name} · {job.status}</option>)}
          </select>}
        </div>}

      </div>
    </div>
    {previewGallery && <EcommerceInputGalleryModal items={previewGallery} onClose={() => setPreviewGallery(null)} />}
  </div>;
}