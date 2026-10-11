import { useRef, type KeyboardEvent } from "react";
import "./RrugcWorkflowNavigation.css";

export type RrugcStageTab = "stage0" | "stage1" | "stage2" | "stage3" | "stage4" | "stage5";
export type RrugcWorkflowGroup = "products" | "reviews";

export const RRUGC_STAGE_TABS: ReadonlyArray<{
  id: RrugcStageTab;
  label: string;
  description: string;
  marker: string;
}> = [
  { id: "stage0", label: "Stage 0", description: "Analysis Keyword", marker: "0" },
  { id: "stage1", label: "Stage 1", description: "Keyword Image Generation", marker: "1" },
  { id: "stage2", label: "Stage 2", description: "Embroidery → 13 Colors", marker: "2" },
  { id: "stage3", label: "Stage 3", description: "Pinterest References", marker: "3" },
  { id: "stage4", label: "Stage 4", description: "Image Generation", marker: "4" },
  { id: "stage5", label: "Stage 5", description: "UGC Review", marker: "5" },
];

export const RRUGC_WORKFLOW_GROUPS: ReadonlyArray<{
  id: RrugcWorkflowGroup;
  title: string;
  subtitle: string;
  stages: readonly RrugcStageTab[];
}> = [
  { id: "products", title: "GatorHats Products", subtitle: "Stages 0–2 · Product artwork", stages: ["stage0", "stage1", "stage2"] },
  { id: "reviews", title: "GatorHats Reviews", subtitle: "Stages 3–5 · Realistic reviews", stages: ["stage3", "stage4", "stage5"] },
];

export function workflowGroupForStage(stage: RrugcStageTab): RrugcWorkflowGroup {
  return stage === "stage0" || stage === "stage1" || stage === "stage2" ? "products" : "reviews";
}

export function stagesForWorkflowGroup(group: RrugcWorkflowGroup) {
  const definition = RRUGC_WORKFLOW_GROUPS.find(item => item.id === group)!;
  return RRUGC_STAGE_TABS.filter(item => definition.stages.includes(item.id));
}

function WorkflowIcon({ group }: { group: RrugcWorkflowGroup }) {
  return group === "products"
    ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="M3 8v8l9 5 9-5V8M12 13v8" />
    </svg>
    : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m12 3 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.8l-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9L12 3Z" />
    </svg>;
}

export function RrugcWorkflowNavigation({
  activeStage,
  onSelectStage,
}: {
  activeStage: RrugcStageTab;
  onSelectStage: (stage: RrugcStageTab) => void;
}) {
  const activeGroup = workflowGroupForStage(activeStage);
  const stages = stagesForWorkflowGroup(activeGroup);
  const lastVisited = useRef<Record<RrugcWorkflowGroup, RrugcStageTab>>({
    products: "stage0",
    reviews: "stage3",
  });
  lastVisited.current[activeGroup] = activeStage;

  function switchGroup(group: RrugcWorkflowGroup) {
    if (group === activeGroup) return;
    onSelectStage(lastVisited.current[group]);
  }

  function onGroupKeyDown(event: KeyboardEvent<HTMLElement>) {
    const index = RRUGC_WORKFLOW_GROUPS.findIndex(item => item.id === activeGroup);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % RRUGC_WORKFLOW_GROUPS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + RRUGC_WORKFLOW_GROUPS.length) % RRUGC_WORKFLOW_GROUPS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = RRUGC_WORKFLOW_GROUPS.length - 1;
    else return;
    event.preventDefault();
    const group = RRUGC_WORKFLOW_GROUPS[next].id;
    switchGroup(group);
    requestAnimationFrame(() => document.getElementById("rrugc-workflow-" + group)?.focus());
  }

  function onStageKeyDown(event: KeyboardEvent<HTMLElement>) {
    const index = stages.findIndex(item => item.id === activeStage);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % stages.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + stages.length) % stages.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = stages.length - 1;
    else return;
    event.preventDefault();
    onSelectStage(stages[next].id);
    requestAnimationFrame(() => document.getElementById("rrugc-tab-" + stages[next].id)?.focus());
  }

  return <div className="rrugc-workflow-navigation">
    <nav className="rrugc-workflow-group-tabs" role="tablist" aria-label="GatorHats workflows" onKeyDown={onGroupKeyDown}>
      {RRUGC_WORKFLOW_GROUPS.map(group =>
        <button
          key={group.id}
          type="button"
          id={"rrugc-workflow-" + group.id}
          className={"rrugc-workflow-group-tab" + (activeGroup === group.id ? " active" : "")}
          role="tab"
          aria-selected={activeGroup === group.id}
          aria-controls={"rrugc-workflow-panel-" + group.id}
          tabIndex={activeGroup === group.id ? 0 : -1}
          onClick={() => switchGroup(group.id)}
        >
          <span className="rrugc-workflow-group-icon"><WorkflowIcon group={group.id} /></span>
          <span className="rrugc-workflow-group-copy"><strong>{group.title}</strong><small>{group.subtitle}</small></span>
          <span className="rrugc-workflow-group-count" aria-hidden="true">3 stages</span>
        </button>,
      )}
    </nav>
    {RRUGC_WORKFLOW_GROUPS.map(group => <div
      key={group.id}
      className="rrugc-workflow-group-panel"
      id={"rrugc-workflow-panel-" + group.id}
      role="tabpanel"
      aria-labelledby={"rrugc-workflow-" + group.id}
      hidden={activeGroup !== group.id}
    >
      {activeGroup === group.id && <nav
        className="ops-tabs rrugc-stage-tabs"
        aria-label={group.title + " stages"}
        role="tablist"
        onKeyDown={onStageKeyDown}
      >
        {stages.map(item => (
          <button
            key={item.id}
            id={"rrugc-tab-" + item.id}
            type="button"
            role="tab"
            aria-selected={activeStage === item.id}
            aria-controls={"rrugc-panel-" + item.id}
            tabIndex={activeStage === item.id ? 0 : -1}
            className={activeStage === item.id ? "active" : ""}
            onClick={() => onSelectStage(item.id)}
          >
            <span className="rrugc-stage-tab-number" aria-hidden="true">{item.marker}</span>
            <span className="rrugc-stage-tab-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
          </button>
        ))}
      </nav>}
    </div>)}
  </div>;
}
