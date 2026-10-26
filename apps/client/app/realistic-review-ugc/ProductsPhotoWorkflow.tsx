import { useEffect, useId, useRef, useState } from "react";
import { formatKeywordJobDuration } from "./KeywordImageStage";
import { getEcommerceInputReadiness, getEcommerceJobLogs, getEcommerceOutputVersions, regenerateEcommerceJob, retryEcommerceJob, runEcommerceJob, runSceneJob, runAllEcommerceJobs, getEcommerceRunAllStatus, type EcommerceRunAllResult, getUgcScoutGallery, startUgcScout, markCandidateReferenceFeedback, type UgcScoutGallery, type ColorwayQueueResult, type EcommerceInputReadiness, type EcommerceJob, type EcommerceJobLogs, type EcommerceOutputVersion, type EcommerceRunAllStep, type ColorwayJob, type ProductsStage2PhotoReadiness } from "./api";
import "./ProductsPhotoWorkflow.css";
import "./MultiHatSceneReferences.css";
import "./stage2-loading.css";
import "./ProductsPhotoWorkflowTwoColumns.css";
import { DeferredImage } from "./DeferredImage";
import { publicHatStockUrl, stage2OriginalUrl, stage2PinterestThumbnailUrl, stage2ThumbnailUrl } from "./stage2Media";
import { EcommerceInputGalleryModal, type InputPreviewItem } from "./EcommerceInputGalleryModal";
import { OutputPhotoGalleryModal, type PhotoOutputItem } from "./OutputPhotoGalleryModal";
import { UgcScoutReviewGallery, type UgcScoutReviewLabel } from "./UgcScoutReviewGallery";
import type { UgcScoutItem } from "./api";
import type { ProductStage2SkillKey } from "./workflowSkillSlots";
import type { Stage2SkillSelection } from "./types";

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
  "natural-black", "natural-brown", "natural-camo-green",
  "natural-charcoal", "natural-forest-green", "natural-khaki", "natural-maroon",
  "natural-mossy-oak-breakup", "natural-navy", "natural-realtree-all-purpose",
  "natural-red", "natural-royal",
] as const;
const PHOTO_COLOR_COUNT = PHOTO_COLOR_KEYS.length;

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

type Props = {
  sourceName: string;
  lastUpdated?: string | null;
  sourcePlanId?: string;
  designSrc?: string;
  stockViews?: Partial<Record<"front" | "side" | "back", string>>;
  selectedColor?: string;
  stockCatalog?: Record<string, { views: Partial<Record<"front" | "side" | "back", string>> }>;
  ecommerceJobs?: EcommerceJob[];
  onEcommerceJobChange?: () => void | Promise<void>;
  readiness: ProductsStage2PhotoReadiness | null;
  colors?: ColorwayJob[];
  colorwaySkillName?: string;
  colorwaySkillKey?: string;
  colorwaySkillSelection?: Stage2SkillSelection | null;
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
  sourceName, lastUpdated, sourcePlanId = "", designSrc = "", stockViews, stockCatalog = {}, selectedColor = "natural-black", ecommerceJobs: allJobs = [], onEcommerceJobChange, readiness, colors = [], colorwaySkillName, colorwaySkillKey = "",
  colorwaySkillOptions = [], colorwaySkillDisabled = false, onColorwaySkillChange, colorwaySkillSelection,
  colorwayReady = false, colorwayRunning = false,
  onQueueColors, onRetryColor, onOpenColor, onOpenLog, configuredSkillNames = {},
}: Props) {
  const selectedStockViews = stockCatalog[selectedColor]?.views || (selectedColor === "natural-black" ? stockViews : undefined);
  const jobs = allJobs.filter(job => (job.color_key || "natural-black") === selectedColor);
  const colorAvailable = selectedColor === "natural-black" || (!!selectedStockViews?.front && !!selectedStockViews?.side && !!selectedStockViews?.back);
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const [ugcScout, setUgcScout] = useState<UgcScoutGallery | null>(null);
  const [ugcScoutError, setUgcScoutError] = useState("");
  const [ugcLearningMessage, setUgcLearningMessage] = useState("");
  const [previewGallery, setPreviewGallery] = useState<InputPreviewItem[] | null>(null);
  const [outputGallery, setOutputGallery] = useState<{ title: string; items: PhotoOutputItem[]; initialIndex: number } | null>(null);
  const [allOutputOpen, setAllOutputOpen] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [runAllState, setRunAllState] = useState<EcommerceRunAllResult | null>(null);
  const [runAllError, setRunAllError] = useState("");
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
  // Every Ecommerce card needs server-backed preflight BEFORE the user opens
  // Details. Previously the fetch only ran inside the expanded accordion, so
  // all four Run buttons appeared unavailable even with complete inputs.
  const ecommerceSkillSignature = PHOTO_STEPS.slice(0, 4)
    .map(step => configuredSkillNames[step.key] || "").join("|");
  const frontJobStatus = jobs.find(job => job.stage === "prod_s2_ec1")?.status || "";
  useEffect(() => {
    setInputs(null);
    if (!sourcePlanId) return;
    const controller = new AbortController();
    let inFlight = false;
    const refresh = async () => {
      if (controller.signal.aborted || inFlight) return;
      inFlight = true;
      try {
        const result = await getEcommerceInputReadiness(sourcePlanId, controller.signal, selectedColor);
        if (!controller.signal.aborted) setInputs(result);
      } catch {
        if (!controller.signal.aborted) setInputs(null);
      } finally {
        inFlight = false;
      }
    };
    void refresh();
    // Poll only while details are open. Front completion and Skill changes
    // also trigger a fresh preflight, so dependent Slots 2/3 unlock promptly.
    const ticker = expandedStep?.startsWith("prod_s2_ec")
      ? window.setInterval(() => void refresh(), 8000) : null;
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      controller.abort();
      if (ticker !== null) window.clearInterval(ticker);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sourcePlanId, expandedStep, selectedColor, ecommerceSkillSignature, frontJobStatus]);

  useEffect(() => {
    if (!sourcePlanId || expandedStep !== "prod_s2_ugc") return;
    const controller = new AbortController();
    const refresh = () => void getUgcScoutGallery(sourcePlanId, controller.signal)
      .then(result => { if (!controller.signal.aborted) { setUgcScout(result); setUgcScoutError(""); } })
      .catch(error => { if (!controller.signal.aborted) setUgcScoutError(error instanceof Error ? error.message : "Scout results unavailable"); });
    refresh();
    const timer = window.setInterval(refresh, 12000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [sourcePlanId, expandedStep]);

  // Bootstrap legacy Stage 2 selections automatically, even when the UGC
  // details accordion is closed. The server owns idempotency and backoff.
  useEffect(() => {
    if (!sourcePlanId) return;
    let cancelled = false;
    void startUgcScout(sourcePlanId)
      .then(() => getUgcScoutGallery(sourcePlanId))
      .then(gallery => { if (!cancelled) { setUgcScout(gallery); setUgcScoutError(""); } })
      .catch(error => { if (!cancelled) setUgcScoutError(error instanceof Error ? error.message : "Automatic Review Scout could not start"); });
    return () => { cancelled = true; };
  }, [sourcePlanId]);

  async function reviewUgcReference(item: UgcScoutItem, label: UgcScoutReviewLabel) {
    if (!ugcScout?.campaign_id) throw new Error("Review Scout campaign is not available.");
    const saved = await markCandidateReferenceFeedback(ugcScout.campaign_id, item.id, label);
    // Apply the saved feedback immediately so a 12s polling refresh cannot
    // make rejected/AI references appear selectable after a successful vote.
    setUgcScout(current => {
      if (!current) return current;
      const items = label === "bad" || label === "ai"
        ? current.items.filter(candidate => candidate.id !== item.id)
        : current.items.map(candidate => candidate.id === item.id
          ? { ...candidate, manual_label: label === "good" ? "good" as const : null }
          : candidate);
      return { ...current, items, count: items.length };
    });
    const learning = saved.learning;
    setUgcLearningMessage(
      (label === "good" ? "✓ Preferred" : label === "bad" ? "✕ Rejected" : label === "ai"
        ? "AI excluded" : "Feedback cleared")
      + " · Saved for Review Scout learning (✓ " + learning.good_count
      + " / ✕ " + learning.bad_count + ")."
    );
    // Refetch canonical labels, avoiding a stale selection after a vote.
    try { setUgcScout(await getUgcScoutGallery(sourcePlanId)); }
    catch { /* The locally updated selection remains correct after the write. */ }
  }

  async function invokeEcommerce(stage: string, job?: EcommerceJob, regenerate = false) {
    if (!sourcePlanId || busyStage) return;
    setBusyStage(stage);
    setJobActionError("");
    try {
      if (job && regenerate) await regenerateEcommerceJob(job.id);
      else if (job && job.status === "failed") await retryEcommerceJob(job.id);
      else await runEcommerceJob(sourcePlanId, stage, selectedColor);
      await onEcommerceJobChange?.();
      setInputs(await getEcommerceInputReadiness(sourcePlanId, undefined, selectedColor));
    } catch (reason) {
      setJobActionError(reason instanceof Error ? reason.message : "Unable to run Ecommerce Skill.");
      setExpandedStep(stage);
    } finally {
      setBusyStage(null);
    }
  }

  async function invokeScene(slot: "prod_s2_mh" | "prod_s2_ugc") {
    if (!sourcePlanId || busyStage) return;
    setBusyStage(slot);
    setJobActionError("");
    try {
      const queued = await runSceneJob(sourcePlanId, slot, selectedColor);
      const blocked = queued.steps.filter(item => item.status === "blocked");
      if (blocked.length) setJobActionError(blocked.map(item => item.reason).join("; "));
      await onEcommerceJobChange?.();
    } catch (reason) {
      setJobActionError(reason instanceof Error ? reason.message : "Unable to queue three images.");
    } finally { setBusyStage(null); }
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
  // The retired Khaki/Maroon output stays in history, not the live gallery.
  const productColors = colors.filter(job => PHOTO_COLOR_KEYS.some(key => key === job.color_key));
  const byColor = new Map(productColors.map(job => [job.color_key, job]));
  // Twelve Natural-series slots, including missing/failed outputs.
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
      const job = jobs.find(item => item.stage === (step.shots.length === 1 ? step.key : step.key + "_" + (shot.number - step.shots[0].number + 1)));
      const status: PhotoOutputItem["status"] = job?.status || "pending";
      return { key: step.key, label: step.title + " · " + shot.label,
        status, src: job?.status === "completed" ? job.output_url : null };
    }).map((item, shotIndex) => ({ ...item, key: item.key + ":" + shotIndex }))),
    ...colorGalleryItems.map(item => ({ ...item, key: "colorway:" + item.key,
      label: "Hat Colors · " + item.label })),
  ];

  // Server-owned durable sequence: browser disconnects do not stop the run.
  useEffect(() => {
    setRunAllState(null);
    setRunAllError("");
    if (!sourcePlanId) return;
    const controller = new AbortController();
    void getEcommerceRunAllStatus(sourcePlanId, selectedColor, controller.signal)
      .then(status => { if (!controller.signal.aborted) setRunAllState(status); })
      .catch(() => { /* Status is optional until the first Run all. */ });
    return () => controller.abort();
  }, [sourcePlanId, selectedColor]);

  useEffect(() => {
    if (runAllState?.status !== "running" || runAllState.source_plan_id !== sourcePlanId
        || runAllState.color_key !== selectedColor) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (document.hidden || controller.signal.aborted) return;
      void getEcommerceRunAllStatus(sourcePlanId, selectedColor, controller.signal)
        .then(status => { if (!controller.signal.aborted) setRunAllState(status); })
        .catch(() => { /* Backend keeps the durable sequence running. */ });
    }, 8000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [sourcePlanId, selectedColor, runAllState?.status]);

  async function runAllOutputs() {
    if (!sourcePlanId || bulkLock.current) return;
    bulkLock.current = true;
    setBulkRunning(true);
    setRunAllError("");
    try {
      // Exactly one POST: the server progresses 1 -> 2 -> ... -> 7. Do not
      // queue 5/6/7 from this browser while an upstream skill is unfinished.
      const started = await runAllEcommerceJobs(sourcePlanId, selectedColor, colorwaySkillSelection);
      setRunAllState(started);
      try { await onEcommerceJobChange?.(); } catch { /* Sequence remains persisted. */ }
    } catch (reason) {
      setRunAllError(reason instanceof Error ? reason.message : "Unable to start sequential Run all.");
    } finally {
      bulkLock.current = false;
      setBulkRunning(false);
    }
  }

  const activeRun = runAllState?.source_plan_id === sourcePlanId &&
    runAllState?.color_key === selectedColor ? runAllState : null;

  const complete = productColors.filter(job => job.status === "completed").length;
  const failed = productColors.filter(job => job.status === "failed").length;
  const active = productColors.filter(job => job.status === "running" || job.status === "queued").length;
  const colorStatus = failed ? failed + " failed" : active ? active + " active" : complete === PHOTO_COLOR_COUNT ? "Completed" : complete > 0 ? complete + "/" + PHOTO_COLOR_COUNT + " done" : "Not run";

  return <div className="rrugc-products-photo-pipeline" aria-label={"Seven-skill photo workflow for " + sourceName}>
    {!colorAvailable && <p role="status">Selected color has no complete synchronized front, side and back stock set. Generation will remain blocked.</p>}
    <div className="rrugc-products-photo-stage-list is-two-column">
      {PHOTO_STEPS.map((step, index) => {
        const state = readiness?.steps.find(item => item.key === step.readinessKey);
        const opened = expandedStep === step.key;
        const configured = configuredSkillNames[step.key];
        const skillName = configured || step.skillName;
        const isEcommerce = index < 4;
        const slotInputs = inputs?.source_plan_id === sourcePlanId && inputs?.color_key === selectedColor
          ? inputs.slots.find(item => item.stage === step.key) : undefined;
        const previewItems: InputPreviewItem[] = (step.inputs || []).map(input => ({
          key: input.kind,
          label: input.label,
          src: input.kind === "design" ? stage2OriginalUrl(designSrc) : input.kind === "embroidered_front"
            ? stage2OriginalUrl(jobs.find(job => job.stage === "prod_s2_ec1")?.output_url || "")
            : input.kind === "front" ? (selectedStockViews?.front || (selectedColor === "natural-black" ? "/rrugc/blueprint/stock/natural-black/front.jpg" : ""))
            : input.kind === "back" ? (selectedStockViews?.back || (selectedColor === "natural-black" ? "/rrugc/blueprint/stock/natural-black/back.jpg" : ""))
            : (selectedStockViews?.side || (selectedColor === "natural-black" ? "/rrugc/blueprint/stock/natural-black/side.jpg" : "")),
        })).filter(item => Boolean(item.src));
        const sceneJobs = isEcommerce ? [] : jobs.filter(job => job.stage.startsWith(step.key + "_"));
        const multiHatReferences = step.key === "prod_s2_mh"
          ? (sceneJobs.find(job => job.scene_references?.length)?.scene_references ?? [])
          : [];
        const shotJob = jobs.find(item => item.stage === step.key);
        const sceneComplete = sceneJobs.length === 3 && sceneJobs.every(job => job.status === "completed");
        const sceneRunnable = !isEcommerce && Boolean(sourcePlanId && configured && state?.ready)
          && !busyStage && !sceneComplete
          && sceneJobs.every(job => !["queued", "running"].includes(job.status));

        const runnable = isEcommerce && Boolean(configured && slotInputs?.ready && sourcePlanId)
          && !busyStage && !shotJob;
        const bundleMissing = !isEcommerce && Boolean(state && !state.bundle_installed);
        const setupMessage = !state
          ? "Checking Skill installation and worker availability…"
          : !configured
            ? "No Skill assigned. Choose an installed image Skill in Manage Skills."
            : state.skill_name !== configured
              ? "Checking the latest assigned Skill. Refresh Skill management if this persists."
              : bundleMissing
                ? "The assigned Skill is unavailable or invalid in the image worker. Check its installation in Manage Skills."
                : state.ready ? "Skill " + configured + " is installed and ready for three-image generation." : "Skill " + configured + " is installed but the worker is unavailable.";

        // Legacy aggregate Ecommerce readiness never makes an individual shot runnable.
        const status = shotJob ? shotJob.status.replace(/^./, char => char.toUpperCase())
          : !configured ? "Needs skill" : isEcommerce
          ? (slotInputs?.ready ? "Ready" : slotInputs?.status === "missing_inputs"
            ? "Missing input" : slotInputs?.status === "worker_pending"
              ? "Worker pending" : "Needs skill")
          : sceneComplete ? "Completed" : sceneJobs.some(job => job.status === "queued" || job.status === "running") ? "Running" : state?.ready ? "Ready" : "Worker pending";
        return <div key={step.key} className="rrugc-products-photo-stage-row" data-skill={step.key}>
          <div className="rrugc-products-photo-stage-title">
            <span className="rrugc-products-photo-number">{index + 1}</span>
            <div><strong>{configured && isEcommerce ? "Ecommerce" : step.title}</strong><small>{configured && isEcommerce ? step.shots[0].label : step.shots.length + (step.shots.length === 1 ? " shot" : " shots")}</small></div>
          </div>
          <div className="rrugc-products-photo-output-strip" role="group" aria-label={step.title + " output slots"}>
            {step.shots.map((shot, shotIndex) => {
              const photo = isEcommerce ? shotJob : sceneJobs.find(job => job.stage === step.key + "_" + (shotIndex + 1));
              return photo?.output_url && photo.status === "completed"
                ? <button type="button" key={shot.number} className="rrugc-products-photo-empty-shot is-completed"
                    onClick={() => setOutputGallery({ title: "All outputs · " + sourceName, items: allOutputItems, initialIndex: allOutputItems.findIndex(item => item.key === step.key + ":" + shotIndex) })}
                    aria-label={"Open " + shot.label + (isEcommerce ? " Ecommerce" : "") + " output"}>
                    <DeferredImage src={stage2ThumbnailUrl(photo.output_url, 128)} alt={shot.label} rootMargin="260px" />
                  </button>
                : <span key={shot.number} className="rrugc-products-photo-empty-shot"
                    title={shot.label + " · " + (photo?.status || "Not generated")}>
                    <HatSlotIcon />
                  </span>;
            })}
          </div>
          <div className="rrugc-products-photo-skill" title={skillName}>
            <span>Skill {index + 1}</span><strong>{skillName}</strong>
          </div>
          <div className="rrugc-products-photo-stage-controls">
            <time className="rrugc-products-photo-updated" title={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleString() : "No update date"} dateTime={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toISOString() : undefined}>{lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleDateString() : "—"}</time>
            <span className="rrugc-products-photo-duration" title="Execution duration from job start to completion">{shotJob ? formatKeywordJobDuration(shotJob.started_at, shotJob.completed_at) : sceneJobs.length ? formatKeywordJobDuration(sceneJobs.map(job => job.started_at).filter(Boolean).sort()[0], sceneComplete ? sceneJobs.map(job => job.completed_at).filter(Boolean).sort().at(-1) : null) : "—"}</span>
            <button type="button" className={"rrugc-products-photo-run-button is-" + (busyStage === step.key || status === "Running" || status === "Queued" ? "processing" : shotJob?.status === "failed" ? "retry" : status === "Completed" ? "completed" : runnable || sceneRunnable ? "ready" : "unavailable")}
              aria-label={"Run " + step.title}
              aria-disabled={isEcommerce ? Boolean(busyStage || (shotJob && (shotJob.status !== "failed" || shotJob.retry_count >= 3))) : Boolean(busyStage || !sceneRunnable)}
              disabled={!!busyStage || (!isEcommerce ? !sceneRunnable : Boolean(shotJob && (shotJob.status !== "failed" || shotJob.retry_count >= 3)))}
              title={!isEcommerce ? setupMessage : runnable ? "Queue Ecommerce image generation" : "Open details for prerequisites and job status"}
              onClick={() => sceneRunnable ? void invokeScene(step.key as "prod_s2_mh" | "prod_s2_ugc") : runnable ? void invokeEcommerce(step.key)
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
                      <DeferredImage src={publicHatStockUrl(stage2ThumbnailUrl(item.src, 256))} alt={input.label} rootMargin="320px" />
                      <span className="rrugc-products-photo-hover-preview" aria-hidden="true"><img src={publicHatStockUrl(stage2ThumbnailUrl(item.src, 256))} alt="" loading="lazy" decoding="async" /></span>
                    </button> : <span>Missing artwork</span>}
                    <small>{input.label}</small>
                  </div>;
                })}
              </div>
              <p>{step.inputInstruction}</p>
              <small>Actual inputs are checked by the server. Stock references are pinned to the released file hashes; generated Front references must complete before Skills 2–3 can run.</small>
            </div>}
            {step.key === "prod_s2_mh" && <section aria-label="Pinned Multi-hat Cap Stack references" className="rrugc-products-photo-ugc-scout">
              <strong>Cap Stack Scout · {multiHatReferences.length}/20 references</strong>
              <p>Automatically samples verified 3-hat and 4-hat photos from GatorHats Reviews Stage 1 when you press Run. Each embroidery design gets an independent random set; all three outputs share its pinned selection.</p>
              {multiHatReferences.length > 0 && <>
                <small>{multiHatReferences.filter(ref => ref.caps_count === 3).length} scenes with 3 hats · {multiHatReferences.filter(ref => ref.caps_count === 4).length} scenes with 4 hats</small>
                <div className="rrugc-multihat-scene-references" aria-label="Selected Multi-hat photos">
                  {multiHatReferences.map((ref, imageIndex) => <a key={ref.image_id}
                    href={ref.pin_url} target="_blank" rel="noopener noreferrer"
                    title={"Reference " + (imageIndex + 1) + " · " + ref.caps_count + " hats"}>
                    <DeferredImage src={stage2PinterestThumbnailUrl(ref.image_url)} alt={"Cap Stack reference with " + ref.caps_count + " hats"}
                      rootMargin="320px" />
                    <small>{ref.caps_count} hats</small>
                  </a>)}
                </div>
              </>}
            </section>}
            {step.key === "prod_s2_ugc" && <section aria-label="Lifestyle Review Scout" className="rrugc-products-photo-ugc-scout">
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
                <strong>Review Scout · Lifestyle references</strong>
                <span>{ugcScout ? ugcScout.count + " / 20 images" : "Checking Scout campaign…"}</span>
                <span role="status">Automatic Scout enabled</span>
                {ugcScout && <small>Scout: {ugcScout.status}{ugcScout.error_code ? " · " + ugcScout.error_code.replace(/_/g, " ") : ""}</small>}
              </div>
              {ugcScoutError && <p role="alert">{ugcScoutError}</p>}
              {ugcLearningMessage && <p role="status" className="rrugc-ugc-learning-message">{ugcLearningMessage}</p>}
              {ugcScout && <UgcScoutReviewGallery
                items={ugcScout.items}
                pinned={Object.fromEntries(sceneJobs.filter(job => job.scene_reference?.candidate_id).map(job => [
                  job.scene_reference!.candidate_id,
                  { slot: job.stage.replace("prod_s2_ugc_", "UGC "), status: job.status },
                ]))}
                onReview={reviewUgcReference}
              />}
              <small>Selected UGC inputs are marked directly on their Scout images. Click an image to vote ✓ / ✕ / AI; feedback teaches Review Scout future preferences. Rejected and AI-tagged images are excluded from new picks. Existing generated outputs remain unchanged.</small>
            </section>}
            {isEcommerce && <div className="rrugc-products-photo-detail-info">
              <strong>{skillName}</strong>
              <p>This Skill generates one independent Ecommerce photo and stores it in managed storage with job logs and output versions.</p>
            </div>}
            <div className="rrugc-products-photo-detail-actions">
            {isEcommerce && <button type="button" onClick={openSkillsManager}>Manage skill</button>}
            {shotJob && <button type="button" disabled={shotJob.status !== "failed" || !!busyStage || shotJob.retry_count >= 3}
              onClick={() => void invokeEcommerce(step.key, shotJob)}>Retry failed</button>}
            {shotJob && <button type="button" onClick={() => void showEcommerceLogs(shotJob.id)}>Logs</button>}
            {shotJob?.output_url && <button type="button" onClick={() => void showEcommerceVersions(shotJob.id)}>Versions</button>}
            {shotJob?.status === "completed" && <button type="button" disabled={!!busyStage}
              onClick={() => void invokeEcommerce(step.key, shotJob, true)}>Regenerate</button>}
            {!shotJob && isEcommerce && <small>Logs available after the first job is queued</small>}
            {!isEcommerce && !state?.ready && <small>Worker or Skill must be ready before running.</small>}
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
          <div><strong>12 Hat Colors</strong><small>{complete}/{PHOTO_COLOR_COUNT} images</small></div>
        </div>
        <div className="rrugc-products-photo-output-strip is-colors" role="group" aria-label="12 hat color output slots" onPointerDown={event => {
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
              ? <DeferredImage src={stage2ThumbnailUrl(job.output_url, 128)} alt={colorName} rootMargin="260px" />
              : <HatSlotIcon />;
            const content = preview;
            const className = "rrugc-colorway-slot is-" + status;
            return <button type="button" key={key} className={className}
              onClick={() => setColorGalleryIndex(index)}
              aria-haspopup="dialog"
              aria-label={"View " + colorName + " in 12 hat colors gallery"}
              title={colorName + " · " + (job?.status || "Not generated") + " · Open all 12 outputs"}>{content}</button>;
          })}
        </div>
        <div className="rrugc-products-photo-skill" title={colorwaySkillName || "Not assigned"}>
          <span>Skill 7</span><strong>{colorwaySkillName || "Not assigned"}</strong>
        </div>
        <div className="rrugc-products-photo-stage-controls">
          <time className="rrugc-products-photo-updated" title={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleString() : "No update date"} dateTime={lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toISOString() : undefined}>{lastUpdated && Number.isFinite(Date.parse(lastUpdated)) ? new Date(lastUpdated).toLocaleDateString() : "—"}</time>
          <span className="rrugc-products-photo-duration" title="Time from the earliest color task start to the last completion">{(() => {
            const starts = productColors.map(job => job.started_at).filter((date): date is string => Boolean(date));
            if (!starts.length) return "—";
            const earliest = starts.reduce((a, b) => Date.parse(a) <= Date.parse(b) ? a : b);
            const ends = productColors.map(job => job.completed_at).filter((date): date is string => Boolean(date));
            const latest = complete === PHOTO_COLOR_COUNT && ends.length === PHOTO_COLOR_COUNT ? ends.reduce((a, b) => Date.parse(a) >= Date.parse(b) ? a : b) : null;
            return formatKeywordJobDuration(earliest, latest);
          })()}</span>
          <button type="button" className={"rrugc-products-photo-run-button is-" + (colorwayRunning || active ? "processing" : failed ? "retry" : complete === PHOTO_COLOR_COUNT ? "completed" : colorwayReady ? "ready" : "unavailable")} aria-label="Run 12 Hat Colors" disabled={!colorwayReady || colorwayRunning || complete === PHOTO_COLOR_COUNT} onClick={() => { void onQueueColors?.(false); }}><ActionIcon kind={colorwayRunning || active ? "loader" : failed ? "retry" : complete === PHOTO_COLOR_COUNT ? "check" : colorwayReady ? "play" : "clock"} />{colorwayRunning ? "Queueing…" : complete === PHOTO_COLOR_COUNT ? "Done" : "Run"}</button>
          <button type="button" className="rrugc-products-photo-detail-toggle"
            aria-expanded={expandedStep === "colorway"} aria-label="12 Hat Colors details"
            onClick={() => setExpandedStep(value => value === "colorway" ? null : "colorway")}>
            <ActionIcon kind="eye" />{expandedStep === "colorway" ? "Hide" : "Details"} <span className="rrugc-details-chevron" aria-hidden="true">{expandedStep === "colorway" ? "⌃" : "⌄"}</span>
          </button>
        </div>
        {expandedStep === "colorway" && <div className="rrugc-products-photo-stage-detail">
          <div className="rrugc-products-photo-colorway-selector">
            <label htmlFor={colorSkillSelectId}>Skill 7 configuration</label>
            <select id={colorSkillSelectId} className="rrugc-products-photo-skill-select"
              aria-label={"12 Hat Colors skill for " + sourceName}
              title={colorwaySkillName || "Choose 12 Hat Colors skill"}
              value={colorwaySkillKey} disabled={colorwaySkillDisabled || !onColorwaySkillChange}
              onChange={event => onColorwaySkillChange?.(event.target.value)}>
              {colorwaySkillOptions.length === 0 && <option value="">{colorwaySkillDisabled ? "Loading skills…" : "No skill available"}</option>}
              {colorwaySkillOptions.map(option => <option key={option.value} value={option.value}>{option.label}{option.ready ? "" : " · needs sync"}</option>)}
            </select>
          </div>
          <div><strong>{colorwaySkillName || "Select colorway skill"}</strong>
            <p>Generate each of 12 Natural-series Valucap 8869 colors. Select any thumbnail to open the full 12-color gallery, including ungenerated images. Use gallery actions to view versions or retry failed outputs.</p></div>
          <button type="button" onClick={openSkillsManager}>Manage skill</button>
          <button type="button" disabled={!colorwayReady || colorwayRunning || complete === PHOTO_COLOR_COUNT}
            onClick={() => { void onQueueColors?.(false); }}>{colorwayRunning ? "Queueing…" : "Run missing"}</button>
          {!!colors.length && onOpenLog && <select aria-label={"Hat color skill logs for " + sourceName} value=""
            onChange={event => { const job = colors.find(item => item.id === event.target.value); if (job) onOpenLog(job); }}>
            <option value="">View logs…</option>
            {productColors.map(job => <option key={job.id} value={job.id}>{job.color_name} · {job.status}</option>)}
          </select>}
        </div>}

      </div>
    </div>
    <div className="rrugc-products-photo-pipeline-summary">
      <div className="rrugc-products-photo-footer-actions" role="group" aria-label="All output actions">
        <button type="button" className="rrugc-products-photo-footer-action is-view"
          onClick={() => setAllOutputOpen(true)} aria-label="View all outputs" title="Browse all 22 output positions, including those not yet generated">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="4" width="7" height="7" rx="1.5" /><rect x="14" y="4" width="7" height="7" rx="1.5" />
            <rect x="3" y="15" width="7" height="6" rx="1.5" /><rect x="14" y="15" width="7" height="6" rx="1.5" />
          </svg>View all outputs
        </button>
        <button type="button" className="rrugc-products-photo-footer-action is-run-all"
          onClick={() => void runAllOutputs()} disabled={!sourcePlanId || bulkRunning || activeRun?.status === "running" || activeRun?.status === "completed"}
          aria-label="Run all" title="Run Slots 1 → 7 sequentially. Each slot finishes every output before the next starts. Completed jobs are reused.">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m8 5 11 7-11 7V5Z" />
          </svg>{bulkRunning ? "Starting…" : activeRun?.status === "running" ? "Slot " + activeRun.current_slot + "/7"
            : activeRun?.status === "blocked" ? "Resume all" : activeRun?.status === "completed" ? "All done" : "Run all"}
        </button>
        <a className="rrugc-products-photo-download-all" href={sourcePlanId ? `/api/v1/realistic-review-ugc/products/source-plans/${encodeURIComponent(sourcePlanId)}/outputs/archive` : undefined} aria-disabled={!sourcePlanId || (!jobs.some(job => job.status === "completed" && job.output_url) && !colors.some(job => job.status === "completed" && job.output_url))} onClick={event => { if (!sourcePlanId || (!jobs.some(job => job.status === "completed" && job.output_url) && !colors.some(job => job.status === "completed" && job.output_url))) event.preventDefault(); }} title="Download all completed images from the 7 skill slots as ZIP"><span className="rrugc-products-photo-download-icon" aria-hidden="true">⇩</span>Download all outputs</a>
      </div>
      {runAllError && <p role="alert" className="rrugc-products-photo-run-all-report">{runAllError}</p>}
      {activeRun && <div className="rrugc-products-photo-run-all-report" role="status" aria-live="polite">
        <strong>Run all · {activeRun.status === "running" ? "Slot " + activeRun.current_slot + "/7"
          : activeRun.status === "completed" ? "Completed 7/7" : "Stopped at Slot " + activeRun.current_slot}</strong>
        <span>{activeRun.status === "running" ? "Running in order. Next slot starts only after this slot completes."
          : activeRun.status === "completed" ? "All outputs are ready."
          : activeRun.error_message || "A slot needs attention before the sequence can resume."}</span>
        <details><summary>View sequential progress</summary>
          <ol>{activeRun.steps.map(step => <li key={step.slot}>
            <b>{step.slot}. {step.name}</b>
            <span>{step.status} · {step.completed_outputs}/{step.total_outputs} outputs</span>
            {step.reason && <small>{step.reason}</small>}
          </li>)}</ol>
        </details>
      </div>}
    </div>
    {previewGallery && <EcommerceInputGalleryModal items={previewGallery} onClose={() => setPreviewGallery(null)} />}
    {outputGallery && <OutputPhotoGalleryModal title={outputGallery.title} items={outputGallery.items} initialIndex={outputGallery.initialIndex}
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
      title={"12 Hat Colors · " + sourceName}
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