import { useEffect, useId, useRef, useState } from "react";
import { formatKeywordJobDuration } from "./KeywordImageStage";
import { getEcommerceInputReadiness, getEcommerceJobLogs, getEcommerceOutputVersions, regenerateEcommerceJob, retryEcommerceJob, runEcommerceJob, runAllEcommerceJobs, type ColorwayQueueResult, type EcommerceInputReadiness, type EcommerceJob, type EcommerceJobLogs, type EcommerceOutputVersion, type EcommerceRunAllStep, type ColorwayJob, type ProductsStage2PhotoReadiness } from "./api";
import "./ProductsPhotoWorkflow.css";
import { EcommerceInputGalleryModal, type InputPreviewItem } from "./EcommerceInputGalleryModal";
import { OutputPhotoGalleryModal, type PhotoOutputItem } from "./OutputPhotoGalleryModal";
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

function ActionIcon({ kind }: { kind: "play" | "retry" | "check" | "clock" | "warning" | "chevron" | "loader" | "eye" }) {
  const symbol = { play: "▶", retry: "↻", check: "✓", clock: "◷", warning: "!", chevron: "⌄", loader: "◌", eye: "▣" }[kind];
  return <span className="rrugc-action-icon" aria-hidden="true">{symbol}</span>;
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

type BulkRunStep = {
  name: string;
  status: EcommerceRunAllStep["status"];
  reason?: string;
};

const BULK_ECOMMERCE_NAMES: Record<string, string> = {
  prod_s2_ec1: "Ecommerce · Image 01",
  prod_s2_ec2: "Ecommerce · Image 02",
  prod_s2_ec3: "Ecommerce · Image 03",
  prod_s2_ec4: "Ecommerce · Image 04",
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
  onQueueColors?: (retryFailedJobs?: boolean) => Promise<ColorwayQueueResult>;
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
  const [outputGallery, setOutputGallery] = useState<{ title: string; items: PhotoOutputItem[] } | null>(null);
  const [allOutputOpen, setAllOutputOpen] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkReport, setBulkReport] = useState<BulkRunStep[] | null>(null);
  const bulkLock = useRef(false);
  const [colorGalleryIndex, setColorGalleryIndex] = useState<number | null>(null);
  const [inputs, setInputs] = useState<EcommerceInputReadiness | null>(null);
  const [busyStage, setBusyStage] = useState<string | null>(null);
  const [jobActionError, setJobActionError] = useState("");
  const [logFor, setLogFor] = useState<string | null>(null);
  const [logs, setLogs] = useState<EcommerceJobLogs | null>(null);
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [versions, setVersions] = useState<EcommerceOutputVersion[]>([]);
  const drag = useRef<{ startX: number; scrollLeft: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
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
  // The gallery always represents all 13 fixed color slots, including the
  // colors that have never been queued or whose jobs failed.
  const colorGalleryItems: PhotoOutputItem[] = PHOTO_COLOR_KEYS.map(key => {
    const job = byColor.get(key);
    return {
      key,
      label: job?.color_name || key.replaceAll("-", " ").replace(/\b\w/g, char => char.toUpperCase()),
      src: job?.status === "completed" ? job.output_url : null,
      status: job?.status || "pending",
    };
  });
  const allOutputItems: PhotoOutputItem[] = [
    ...PHOTO_STEPS.flatMap(step => step.shots.map(shot => {
      const job = jobs.find(item => item.stage === step.key);
      const status: PhotoOutputItem["status"] = job?.status || "pending";
      return { key: step.key, label: step.title + " · " + shot.label,
        status, src: job?.status === "completed" ? job.output_url : null };
    }).map((item, shotIndex) => ({ ...item, key: item.key + ":" + shotIndex }))),
    ...colorGalleryItems.map(item => ({ ...item, key: "colorway:" + item.key,
      label: "Hat Colors · " + item.label })),
  ];

  async function runAllOutputs() {
    if (!sourcePlanId || bulkLock.current) return;
    bulkLock.current = true;
    setBulkRunning(true);
    setBulkReport(null);
    let steps: BulkRunStep[] = [];
    try {
      try {
        const queued = await runAllEcommerceJobs(sourcePlanId);
        steps = queued.steps.map(item => ({
          name: BULK_ECOMMERCE_NAMES[item.stage] || item.stage,
          status: item.status, reason: item.reason,
        }));
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "Unable to queue Ecommerce images.";
        steps = Object.values(BULK_ECOMMERCE_NAMES).map(name => ({
          name, status: "blocked", reason: message,
        }));
      }
      for (const [key, name] of [["multihat", "Multi-hat"], ["ugc", "UGC"]] as const) {
        const skill = readiness?.steps.find(step => step.key === key);
        steps.push({ name, status: "blocked", reason: !skill
          ? "Skill availability is not confirmed."
          : !skill.bundle_installed
            ? "Approved Skill ZIP or reference assets are missing."
            : "The three-image worker has not been enabled." });
      }
      if (complete === 13) {
        steps.push({ name: "13 Hat Colors", status: "completed" });
      } else if (colorwayRunning && active > 0) {
        steps.push({ name: "13 Hat Colors", status: "running",
          reason: "Existing colorway jobs are still running." });
      } else if (!colorwayReady || !onQueueColors) {
        steps.push({ name: "13 Hat Colors", status: "blocked",
          reason: "Select a ready Skill with all 13 verified stock images." });
      } else {
        try {
          const result = await onQueueColors(true);
          const newlyQueued = result.queued + (result.retried || 0);
          steps.push({ name: "13 Hat Colors",
            status: newlyQueued > 0 ? "queued" : result.error ? "blocked"
              : complete === 13 ? "completed" : "running",
            reason: result.error ||
              (newlyQueued > 0 ? "Queued " + newlyQueued + " image job(s)."
                : "Existing image jobs retained; no duplicates queued.") });
        } catch (reason) {
          steps.push({ name: "13 Hat Colors", status: "blocked",
            reason: reason instanceof Error ? reason.message : "Could not start Hat Colors." });
        }
      }
      setBulkReport(steps);
      try { await onEcommerceJobChange?.(); } catch { /* Preserve the server queue result. */ }
    } finally {
      bulkLock.current = false;
      setBulkRunning(false);
    }
  }

  const complete = colors.filter(job => job.status === "completed").length;
  const failed = colors.filter(job => job.status === "failed").length;
  const active = colors.filter(job => job.status === "running" || job.status === "queued").length;
  const colorStatus = failed ? failed + " failed" : active ? active + " active" : complete === 13 ? "Completed" : complete > 0 ? complete + "/13 done" : "Not run";

  return <div className="rrugc-products-photo-pipeline" aria-label={"Seven-skill photo workflow for " + sourceName}>
    <div className="rrugc-products-photo-stage-list">
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
        const bundleMissing = !isEcommerce && Boolean(state && !state.bundle_installed);
        const setupMessage = !state
          ? "Checking Skill installation and worker availability…"
          : bundleMissing
            ? "The exact approved Skill ZIP is not installed. Upload it in Manage Skills."
            : "Skill assets are installed, but the three-image generation worker is not enabled yet.";

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
                  onClick={() => setOutputGallery({ title: shot.label + " · output", items: [{ key: shotJob.id, label: shot.label, src: shotJob.output_url!, status: "completed" }] })}
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
            <span className="rrugc-products-photo-duration" title="Execution duration from job start to completion">Duration: {shotJob ? formatKeywordJobDuration(shotJob.started_at, shotJob.completed_at) : "—"}</span>
            <button type="button" className={"rrugc-products-photo-run-button is-" + (busyStage === step.key || status === "Running" || status === "Queued" ? "processing" : shotJob?.status === "failed" ? "retry" : status === "Completed" ? "completed" : runnable ? "ready" : "unavailable")}
              aria-label={"Run " + step.title}
              aria-disabled={!runnable && !(shotJob?.status === "failed" && shotJob.retry_count < 3)}
              disabled={!isEcommerce || !!busyStage || Boolean(shotJob && (shotJob.status !== "failed" || shotJob.retry_count >= 3))}
              title={!isEcommerce ? setupMessage : runnable ? "Queue Ecommerce image generation" : "Open details for prerequisites and job status"}
              onClick={() => runnable ? void invokeEcommerce(step.key)
                : isEcommerce && shotJob?.status === "failed" ? void invokeEcommerce(step.key, shotJob)
                : setExpandedStep(step.key)}><ActionIcon kind={busyStage === step.key || status === "Running" || status === "Queued" ? "loader" : shotJob?.status === "failed" ? "retry" : status === "Completed" ? "check" : !runnable ? "clock" : "play"} />{busyStage === step.key ? "Queueing…" : shotJob?.status === "failed" ? "Retry" : status === "Completed" ? "Done" : status === "Running" || status === "Queued" ? "Running" : "Run"}</button>
            <button type="button" className="rrugc-products-photo-detail-toggle"
              aria-expanded={opened} aria-label={step.title + " details"}
              onClick={() => setExpandedStep(opened ? null : step.key)}><ActionIcon kind="eye" />{opened ? "Hide" : "Details"} <span className="rrugc-details-chevron" aria-hidden="true">{opened ? "⌃" : "⌄"}</span></button>
          </div>
          {opened && <div className={"rrugc-products-photo-stage-detail " + (isEcommerce ? "is-ecommerce-detail" : "is-bundled-skill-detail")}>
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
            <div className="rrugc-products-photo-detail-info">
              {isEcommerce ? <><strong>{skillName}</strong>
                <p>This Skill generates one independent Ecommerce photo and stores it in managed storage with job logs and output versions.</p>
              </> : <>
                <strong>{step.title} · {skillName}</strong>
                <p className="rrugc-products-photo-bundle-status" role="status">{setupMessage}</p>
                <p className="rrugc-products-photo-bundle-inputs"><b>Required references:</b> {state?.required_inputs || "Verified embroidery artwork, factory stock and scene references."}</p>
                {bundleMissing && Boolean(state?.missing_assets?.length) && <details className="rrugc-products-photo-bundle-missing">
                  <summary>Missing package files ({state!.missing_assets.length})</summary>
                  <ul>{state!.missing_assets.map(asset => <li key={asset}><code>{asset}</code></li>)}</ul>
                </details>}
              </>}
            </div>
            <div className="rrugc-products-photo-detail-actions">
            <button type="button" onClick={openSkillsManager}>{!isEcommerce && bundleMissing ? "Upload Skill ZIP" : "Manage skill"}</button>
            {shotJob && <button type="button" disabled={shotJob.status !== "failed" || !!busyStage || shotJob.retry_count >= 3}
              onClick={() => void invokeEcommerce(step.key, shotJob)}>Retry failed</button>}
            {shotJob && <button type="button" onClick={() => void showEcommerceLogs(shotJob.id)}>Logs</button>}
            {shotJob?.output_url && <button type="button" onClick={() => void showEcommerceVersions(shotJob.id)}>Versions</button>}
            {shotJob?.status === "completed" && <button type="button" disabled={!!busyStage}
              onClick={() => void invokeEcommerce(step.key, shotJob, true)}>Regenerate</button>}
            {!shotJob && isEcommerce && <small>Logs available after the first job is queued</small>}
            {!isEcommerce && <small>Run stays disabled until the original ZIP and a verified three-image worker are available.</small>}
            </div>
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
        <div className="rrugc-products-photo-output-strip is-colors" role="group" aria-label="13 hat color output slots" onPointerDown={event => {
          if (event.pointerType === "touch") return;
          const el = event.currentTarget;
          drag.current = { startX: event.clientX, scrollLeft: el.scrollLeft, moved: false };
        }} onPointerMove={event => {
          if (!drag.current || event.buttons !== 1) return;
          const dx = event.clientX - drag.current.startX;
          if (Math.abs(dx) > 5) drag.current.moved = true;
          if (drag.current.moved) { event.currentTarget.scrollLeft = drag.current.scrollLeft - dx; event.preventDefault(); }
        }} onPointerUp={() => { suppressClick.current = Boolean(drag.current?.moved); drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onClickCapture={event => {
          if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; }
        }}>
          {PHOTO_COLOR_KEYS.map((key, index) => {
            const job = byColor.get(key);
            const colorName = job?.color_name || key.replaceAll("-", " ");
            const status = job?.status || "pending";
            const preview = job?.output_url && job.status === "completed"
              ? <img loading="lazy" src={job.output_url} alt={colorName} />
              : <HatSlotIcon />;
            const content = preview;
            const className = "rrugc-colorway-slot is-" + status;
            return <button type="button" key={key} className={className}
              onClick={() => setColorGalleryIndex(index)}
              aria-haspopup="dialog"
              aria-label={"View " + colorName + " in 13 hat colors gallery"}
              title={colorName + " · " + (job?.status || "Not generated") + " · Open all 13 outputs"}>{content}</button>;
          })}
        </div>
        <div className="rrugc-products-photo-skill" title={colorwaySkillName || "Not assigned"}>
          <span>Skill 7</span><strong>{colorwaySkillName || "Not assigned"}</strong>
        </div>
        <div className="rrugc-products-photo-stage-controls">
          <time className="rrugc-products-photo-updated" title={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleString() : "No update date"} dateTime={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toISOString() : undefined}>Last updated: {lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleDateString() : "—"}</time>
          <span className="rrugc-products-photo-duration" title="Time from the earliest color task start to the last completion">Duration: {(() => {
            const starts = colors.map(job => job.started_at).filter((date): date is string => Boolean(date));
            if (!starts.length) return "—";
            const earliest = starts.reduce((a, b) => Date.parse(a) <= Date.parse(b) ? a : b);
            const ends = colors.map(job => job.completed_at).filter((date): date is string => Boolean(date));
            const latest = complete === 13 && ends.length === 13 ? ends.reduce((a, b) => Date.parse(a) >= Date.parse(b) ? a : b) : null;
            return formatKeywordJobDuration(earliest, latest);
          })()}</span>
          <button type="button" className={"rrugc-products-photo-run-button is-" + (colorwayRunning || active ? "processing" : failed ? "retry" : complete === 13 ? "completed" : colorwayReady ? "ready" : "unavailable")} aria-label="Run 13 Hat Colors" disabled={!colorwayReady || colorwayRunning || complete === 13} onClick={() => { void onQueueColors?.(false); }}><ActionIcon kind={colorwayRunning || active ? "loader" : failed ? "retry" : complete === 13 ? "check" : colorwayReady ? "play" : "clock"} />{colorwayRunning ? "Queueing…" : complete === 13 ? "Done" : "Run"}</button>
          <button type="button" className="rrugc-products-photo-detail-toggle"
            aria-expanded={expandedStep === "colorway"} aria-label="13 Hat Colors details"
            onClick={() => setExpandedStep(value => value === "colorway" ? null : "colorway")}>
            <ActionIcon kind="eye" />{expandedStep === "colorway" ? "Hide" : "Details"} <span className="rrugc-details-chevron" aria-hidden="true">{expandedStep === "colorway" ? "⌃" : "⌄"}</span>
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
            <p>Generate each of 13 Valucap 8869 colors. Select any thumbnail to open the full 13-color gallery, including ungenerated images. Use gallery actions to view versions or retry failed outputs.</p></div>
          <button type="button" onClick={openSkillsManager}>Manage skill</button>
          <button type="button" disabled={!colorwayReady || colorwayRunning || complete === 13}
            onClick={() => { void onQueueColors?.(false); }}>{colorwayRunning ? "Queueing…" : "Run missing"}</button>
          {!!colors.length && onOpenLog && <select aria-label={"Hat color skill logs for " + sourceName} value=""
            onChange={event => { const job = colors.find(item => item.id === event.target.value); if (job) onOpenLog(job); }}>
            <option value="">View logs…</option>
            {colors.map(job => <option key={job.id} value={job.id}>{job.color_name} · {job.status}</option>)}
          </select>}
        </div>}

      </div>
    </div>
    <div className="rrugc-products-photo-pipeline-summary">
      <div className="rrugc-products-photo-footer-actions" role="group" aria-label="All output actions">
        <button type="button" className="rrugc-products-photo-footer-action is-view"
          onClick={() => setAllOutputOpen(true)} aria-label="View all outputs" title="Browse all 23 output positions, including those not yet generated">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="4" width="7" height="7" rx="1.5" /><rect x="14" y="4" width="7" height="7" rx="1.5" />
            <rect x="3" y="15" width="7" height="6" rx="1.5" /><rect x="14" y="15" width="7" height="6" rx="1.5" />
          </svg>View all outputs
        </button>
        <button type="button" className="rrugc-products-photo-footer-action is-run-all"
          onClick={() => void runAllOutputs()} disabled={!sourcePlanId || bulkRunning} aria-label="Run all"
          title="Run all ready Skills 1–7; unavailable Skill workers are reported as blocked">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m8 5 11 7-11 7V5Z" />
          </svg>{bulkRunning ? "Queueing…" : "Run all"}
        </button>
        <a className="rrugc-products-photo-download-all" href={sourcePlanId ? `/api/v1/realistic-review-ugc/products/source-plans/${encodeURIComponent(sourcePlanId)}/outputs/archive` : undefined} aria-disabled={!sourcePlanId || (!jobs.some(job => job.status === "completed" && job.output_url) && !colors.some(job => job.status === "completed" && job.output_url))} onClick={event => { if (!sourcePlanId || (!jobs.some(job => job.status === "completed" && job.output_url) && !colors.some(job => job.status === "completed" && job.output_url))) event.preventDefault(); }} title="Download all completed images from the 7 skill slots as ZIP"><span className="rrugc-products-photo-download-icon" aria-hidden="true">⇩</span>Download all outputs</a>
      </div>
      {bulkReport && <div className="rrugc-products-photo-run-all-report" role="status" aria-live="polite">
        <strong>Run all:</strong>
        <span>{bulkReport.filter(step => step.status === "queued").length} queued · {bulkReport.filter(step => step.status === "waiting_for_front").length} waiting · {bulkReport.filter(step => step.status === "completed" || step.status === "running").length} existing · {bulkReport.filter(step => step.status === "blocked").length} blocked</span>
        <details><summary>View results for all 7 Skills</summary>
          <ul>{bulkReport.map(step => <li key={step.name}>
            <b>{step.name}</b>
            <span>{step.status === "waiting_for_front" ? "Waiting for Image 01" : step.status}</span>
            {step.reason && <small>{step.reason}</small>}
          </li>)}</ul>
        </details>
      </div>}
    </div>
    {previewGallery && <EcommerceInputGalleryModal items={previewGallery} onClose={() => setPreviewGallery(null)} />}
    {outputGallery && <OutputPhotoGalleryModal title={outputGallery.title} items={outputGallery.items}
      onClose={() => setOutputGallery(null)} />}
    {allOutputOpen && <OutputPhotoGalleryModal title={"All outputs · " + sourceName} items={allOutputItems}
      onClose={() => setAllOutputOpen(false)}
      onOpenVersions={item => {
        if (item.key.startsWith("colorway:")) {
          const job = byColor.get(item.key.slice("colorway:".length));
          if (!job || !onOpenColor) return;
          setAllOutputOpen(false);
          onOpenColor(job);
        } else {
          const job = jobs.find(value => value.stage === item.key.split(":")[0]);
          if (!job || !job.output_url) return;
          setAllOutputOpen(false);
          setExpandedStep(job.stage);
          void showEcommerceVersions(job.id);
        }
      }}
      onRetry={item => {
        if (item.key.startsWith("colorway:")) {
          const job = byColor.get(item.key.slice("colorway:".length));
          if (!job || !onRetryColor || colorwayRunning || job.retry_count >= 3) return;
          setAllOutputOpen(false);
          onRetryColor(job);
        } else {
          const job = jobs.find(value => value.stage === item.key.split(":")[0]);
          if (!job || job.status !== "failed" || job.retry_count >= 3 || busyStage) return;
          setAllOutputOpen(false);
          void invokeEcommerce(job.stage, job);
        }
      }}
      retryDisabled={item => item.key.startsWith("colorway:")
        ? colorwayRunning || (byColor.get(item.key.slice("colorway:".length))?.retry_count || 0) >= 3
        : Boolean(busyStage || (jobs.find(value => value.stage === item.key.split(":")[0])?.retry_count || 0) >= 3)}
    />}
    {colorGalleryIndex !== null && <OutputPhotoGalleryModal
      title={"13 Hat Colors · " + sourceName}
      items={colorGalleryItems} initialIndex={colorGalleryIndex}
      onClose={() => setColorGalleryIndex(null)}
      onOpenVersions={item => {
        const job = byColor.get(item.key);
        if (!job || !onOpenColor) return;
        setColorGalleryIndex(null);
        onOpenColor(job);
      }}
      onRetry={item => {
        const job = byColor.get(item.key);
        if (!job || !onRetryColor || colorwayRunning || job.retry_count >= 3) return;
        setColorGalleryIndex(null);
        onRetryColor(job);
      }}
      retryDisabled={item => colorwayRunning || (byColor.get(item.key)?.retry_count || 0) >= 3}
    />}
  </div>;
}