import { useId, useState } from "react";
import type { ColorwayJob, ProductsStage2PhotoReadiness } from "./api";
import "./ProductsPhotoWorkflow.css";
import type { ProductStage2SkillKey } from "./workflowSkillSlots";

type StepDefinition = {
  key: ProductStage2SkillKey;
  readinessKey: "ecommerce" | "multihat" | "ugc";
  title: string;
  skillName: string;
  shots: ReadonlyArray<{ label: string; number: number }>;
  inputs?: ReadonlyArray<{ label: string; kind: "design" | "front" | "angle" | "side" | "back" }>;
  inputInstruction?: string;
};

/** Explicit 1-to-1 Skill → Ecommerce photo mapping; ten existing photos total. */
const PHOTO_STEPS: StepDefinition[] = [
  { key: "prod_s2_ec1", readinessKey: "ecommerce", title: "Ecommerce · Image 01", skillName: "Not assigned", shots: [{ label: "Front High", number: 1 }], inputs: [{ label: "Embroidery artwork", kind: "design" }, { label: "Front stock", kind: "front" }], inputInstruction: "Hat Front Embroidery: preserve the original design within the calibrated front embroidery area." },
  { key: "prod_s2_ec2", readinessKey: "ecommerce", title: "Ecommerce · Image 02", skillName: "Not assigned", shots: [{ label: "Front Right 3/4", number: 2 }], inputs: [{ label: "Embroidery artwork", kind: "design" }, { label: "Front embroidered reference", kind: "front" }, { label: "Three-quarter hat", kind: "angle" }], inputInstruction: "Hat ThreeQuarter Embroidery: match the front design and cap color while adjusting placement to the angled crown." },
  { key: "prod_s2_ec3", readinessKey: "ecommerce", title: "Ecommerce · Image 03", skillName: "Not assigned", shots: [{ label: "Right Side", number: 3 }], inputs: [{ label: "Embroidery artwork", kind: "design" }, { label: "Front embroidered reference", kind: "front" }, { label: "Side-angle hat", kind: "side" }], inputInstruction: "Hat Side90 Embroidery: preserve the design, scaled for the visible side perspective, without inventing lettering." },
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
  designSrc?: string;
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
  sourceName, designSrc = "", readiness, colors = [], colorwaySkillName, colorwaySkillKey = "",
  colorwaySkillOptions = [], colorwaySkillDisabled = false, onColorwaySkillChange,
  colorwayReady = false, colorwayRunning = false,
  onQueueColors, onRetryColor, onOpenColor, onOpenLog, configuredSkillNames = {},
}: Props) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
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
    </div>
    <div className="rrugc-products-photo-stage-list">
      <div className="rrugc-products-photo-group-label">Ecommerce · 4 individual image Skills</div>
      {PHOTO_STEPS.map((step, index) => {
        const state = readiness?.steps.find(item => item.key === step.readinessKey);
        const opened = expandedStep === step.key;
        const configured = configuredSkillNames[step.key];
        const skillName = configured || step.skillName;
        const isEcommerce = index < 4;
        // Legacy aggregate Ecommerce readiness never makes an individual shot runnable.
        const status = !configured ? "Needs skill" : isEcommerce ? "Worker pending" :
          state?.ready ? "Ready" : "Worker pending";
        return <div key={step.key} className="rrugc-products-photo-stage-row" data-skill={step.key}>
          <div className="rrugc-products-photo-stage-title">
            <span className="rrugc-products-photo-number">{index + 1}</span>
            <div><strong>{step.title}</strong><small>{step.shots.length} {step.shots.length === 1 ? "shot" : "shots"}</small></div>
          </div>
          <div className="rrugc-products-photo-output-strip" role="group" aria-label={step.title + " output slots"}>
            {step.shots.map(shot => <span key={shot.number} className="rrugc-products-photo-empty-shot"
              title={shot.label + " · Not generated"} aria-label={shot.label + " · Not generated"}>
              <HatSlotIcon /><b>{String(shot.number).padStart(2, "0")}</b>
            </span>)}
          </div>
          <div className="rrugc-products-photo-skill" title={skillName}>
            <span>Skill {index + 1}</span><strong>{skillName}</strong>
          </div>
          <div className="rrugc-products-photo-stage-controls">
            <span className={"rrugc-products-photo-state " + (status === "Ready" ? "is-done" : "is-unavailable")}
              title={isEcommerce
                ? "One Skill per Ecommerce image. No per-image execution worker has been enabled."
                : state?.required_inputs || "Photo generation worker is not enabled"}>{status}</span>
            <button type="button" className="rrugc-products-photo-detail-toggle"
              aria-expanded={opened} aria-label={step.title + " details"}
              onClick={() => setExpandedStep(opened ? null : step.key)}>{opened ? "Hide" : "Details"} <span aria-hidden="true">{opened ? "⌃" : "⌄"}</span></button>
          </div>
          {opened && <div className="rrugc-products-photo-stage-detail">
            {isEcommerce && <div className="rrugc-products-photo-inputs" aria-label={step.title + " input references"}>
              <strong>Input references · {step.inputs?.length}</strong>
              <div className="rrugc-products-photo-input-grid">
                {step.inputs?.map(input => {
                  const src = input.kind === "design" ? designSrc :
                    input.kind === "front" ? "/rrugc/blueprint/fronts/natural-black.webp" :
                    input.kind === "back" ? "/rrugc/blueprint/stock/natural-black/back.jpg" :
                    "/rrugc/blueprint/stock/natural-black/side.jpg";
                  return <div className="rrugc-products-photo-input" key={input.kind}>
                    {src ? <img src={src} alt={input.label} loading="lazy" /> : <span>Missing artwork</span>}
                    <small>{input.label}</small>
                  </div>;
                })}
              </div>
              <p>{step.inputInstruction}</p>
              <small>Preview references only. Saved embroidered input assets and a verified worker are required before running.</small>
            </div>}
            <div><strong>{skillName}</strong>
              <p>{isEcommerce
                ? "This Skill is assigned to " + step.title + " only. Output persists independently once a verified per-image worker is enabled."
                : state?.required_inputs || "Requires verified stock and reference photography. Execution has not been enabled."}</p>
              {!!state?.missing_assets?.length && <small>Missing assets: {state.missing_assets.join(", ")}</small>}
            </div>
            <button type="button" onClick={openSkillsManager}>Manage skill</button>
            <button type="button" disabled title="Photo execution requires separate worker, persistence and end-to-end approval.">Run / Retry / Logs</button>
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
            const content = <>{preview}<b>{String(index + 1).padStart(2, "0")}</b></>;
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
        <div className="rrugc-products-photo-skill">
          <label htmlFor={colorSkillSelectId}><span>Skill 7</span></label>
          <select id={colorSkillSelectId} className="rrugc-products-photo-skill-select"
            aria-label={"13 Hat Colors skill for " + sourceName}
            title={colorwaySkillName || "Choose 13 Hat Colors skill"}
            value={colorwaySkillKey} disabled={colorwaySkillDisabled || !onColorwaySkillChange}
            onChange={event => onColorwaySkillChange?.(event.target.value)}>
            {colorwaySkillOptions.length === 0 && <option value="">{colorwaySkillDisabled ? "Loading skills…" : "No skill available"}</option>}
            {colorwaySkillOptions.map(option => <option key={option.value} value={option.value}>{option.label}{option.ready ? "" : " · needs sync"}</option>)}
          </select>
        </div>
        <div className="rrugc-products-photo-stage-controls">
          <span className={"rrugc-products-photo-state " + (failed ? "is-failed" : active ? "is-running" : complete === 13 ? "is-done" : "")}>
            {colorStatus}
          </span>
          <button type="button" className="rrugc-products-photo-detail-toggle"
            aria-expanded={expandedStep === "colorway"} aria-label="13 Hat Colors details"
            onClick={() => setExpandedStep(value => value === "colorway" ? null : "colorway")}>
            {expandedStep === "colorway" ? "Hide" : "Details"} <span aria-hidden="true">{expandedStep === "colorway" ? "⌃" : "⌄"}</span>
          </button>
        </div>
        {expandedStep === "colorway" && <div className="rrugc-products-photo-stage-detail">
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
  </div>;
}