import { useState } from "react";
import type { ProductsStage2PhotoReadiness } from "./api";
import "./ProductsPhotoWorkflow.css";

type StepDefinition = {
  key: "ecommerce" | "multihat" | "ugc";
  title: string;
  count: number;
  skillName: string;
  shots: string[];
};

const STEPS: StepDefinition[] = [
  { key: "ecommerce", title: "Ecommerce", count: 4, skillName: "gatorhats-8869-ecommerce-v2-4",
    shots: ["01 · Front High", "02 · Front Right 3/4", "03 · Right Side", "04 · Back High"] },
  { key: "multihat", title: "Multi-hat", count: 3, skillName: "gatorhats-8869-multihat-v2-4",
    shots: ["05 · Multi-hat", "06 · Multi-hat", "07 · Multi-hat"] },
  { key: "ugc", title: "UGC", count: 3, skillName: "gatorhats-8869-ugc-v3",
    shots: ["08 · UGC", "09 · UGC", "10 · UGC"] },
];

function openSkillsManager() {
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>(".rrugc-global-management-button"))
    .find(item => item.textContent?.includes("Manage skills"));
  button?.click();
}

export function ProductsPhotoWorkflow({
  sourceName, readiness,
}: { sourceName: string; readiness: ProductsStage2PhotoReadiness | null }) {
  const [expanded, setExpanded] = useState(false);
  const info = STEPS.map(step => ({
    ...step,
    state: readiness?.steps.find(item => item.key === step.key),
  }));
  return <div className="rrugc-products-photo-pipeline">
    <div className="rrugc-products-photo-pipeline-steps" role="group" aria-label={"Three-skill photo workflow for " + sourceName}>
      {info.map((step, index) => <span key={step.key}
        className="rrugc-products-photo-step is-needs-input"
        title={step.skillName + " · " + (step.state?.status === "needs_pipeline_worker"
          ? "Skill installed · execution pipeline not yet enabled"
          : "Skill bundle not installed")}>
        <b>{index + 1}</b>{step.title}<small>{step.count}</small>
      </span>)}
      <button type="button" className="rrugc-products-photo-expand"
        aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}
      >{expanded ? "Hide" : "Details"} <span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span></button>
    </div>
    {expanded && <div className="rrugc-products-photo-details">
      <strong>10-photo pipeline · sequential</strong>
      <p>The original 13-color batch stays unchanged. Each skill needs its exact ZIP bundle and real photo references before execution can be enabled.</p>
      <div className="rrugc-products-photo-stage-grid">
        {info.map((step, index) => <article key={step.key} className="rrugc-products-photo-stage-card">
          <header><b>{String(index + 1).padStart(2, "0")} · {step.title}</b>
            <span>{step.state?.bundle_installed ? "Skill installed" : "Needs skill"}</span></header>
          <small title={step.skillName}>{step.skillName}</small>
          <div className="rrugc-products-photo-shot-list">
            {step.shots.map(shot => <span key={shot} title="Not generated">{shot} · —</span>)}
          </div>
          <p>{step.state?.required_inputs || "Requires real stock and scene references."}</p>
          {Boolean(step.state?.missing_assets?.length) && <small title={step.state?.missing_assets.join(", ")}>
            Missing {step.state?.missing_assets.length} required skill files
          </small>}
          <div className="rrugc-products-photo-stage-actions">
            <button type="button" onClick={openSkillsManager}>Manage skills</button>
            <button type="button" disabled title="Execution, logs and retries will become available only after the pipeline worker is enabled.">Run / Retry / Logs</button>
          </div>
        </article>)}
      </div>
    </div>}
  </div>;
}
