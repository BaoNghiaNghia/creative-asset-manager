import { type KeyboardEvent } from "react";
import { stagesForWorkflowGroup, workflowGroupForStage, type RrugcStageTab } from "./workflowRouting";
import "./RrugcWorkflowNavigation.css";

export type { RrugcStageTab, RrugcWorkflowGroup } from "./workflowRouting";
export { RRUGC_STAGE_TABS, RRUGC_WORKFLOW_GROUPS, workflowGroupForStage, stagesForWorkflowGroup } from "./workflowRouting";

/** Stage navigation stays in the page; Product/Review switching lives in the global sidebar. */
export function RrugcWorkflowNavigation({
  activeStage,
  onSelectStage,
}: {
  activeStage: RrugcStageTab;
  onSelectStage: (stage: RrugcStageTab) => void;
}) {
  const group = workflowGroupForStage(activeStage);
  const stages = stagesForWorkflowGroup(group);

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
    <nav className="ops-tabs rrugc-stage-tabs" aria-label={(group === "products" ? "GatorHats Products" : "GatorHats Reviews") + " stages"} role="tablist" onKeyDown={onStageKeyDown}>
      {stages.map(item => <button
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
      </button>)}
    </nav>
  </div>;
}
