// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  RrugcWorkflowNavigation, RRUGC_WORKFLOW_GROUPS,
  stagesForWorkflowGroup, workflowGroupForStage, type RrugcStageTab,
} from "./RrugcWorkflowNavigation";

describe("GatorHats Products / Reviews workflow navigation", () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    function Harness() {
      const [stage, setStage] = useState<RrugcStageTab>("stage0");
      return <>
        <RrugcWorkflowNavigation activeStage={stage} onSelectStage={setStage} />
        <output data-testid="active-stage">{stage}</output>
      </>;
    }
    await act(async () => { root.render(<Harness />); });
  });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    host.remove();
  });

  const selected = () => host.querySelector<HTMLOutputElement>('[data-testid="active-stage"]')?.textContent;
  const tabs = () => Array.from(host.querySelectorAll<HTMLButtonElement>('.rrugc-stage-tabs button')).map(tab => tab.id);

  it("places stages 0–2 exclusively in Products and 3–5 exclusively in Reviews", () => {
    expect(RRUGC_WORKFLOW_GROUPS.map(item => item.title)).toEqual(["GatorHats Products", "GatorHats Reviews"]);
    expect(stagesForWorkflowGroup("products").map(item => item.id)).toEqual(["stage0", "stage1", "stage2"]);
    expect(stagesForWorkflowGroup("reviews").map(item => item.id)).toEqual(["stage3", "stage4", "stage5"]);
    expect(workflowGroupForStage("stage5")).toBe("reviews");
    expect(selected()).toBe("stage0");
    expect(tabs()).toEqual(["rrugc-tab-stage0", "rrugc-tab-stage1", "rrugc-tab-stage2"]);
    expect(host.querySelector('[role="tab"][aria-selected="true"]')?.id).toBe("rrugc-workflow-products");
  });

  it("switches groups, selects stages and restores the most recent stage per group", async () => {
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-tab-stage2")?.click(); });
    expect(selected()).toBe("stage2");
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-workflow-reviews")?.click(); });
    expect(selected()).toBe("stage3");
    expect(tabs()).toEqual(["rrugc-tab-stage3", "rrugc-tab-stage4", "rrugc-tab-stage5"]);
    expect(host.querySelector("#rrugc-tab-stage0")).toBeNull();
    expect(host.querySelector("#rrugc-workflow-panel-products")?.hasAttribute("hidden")).toBe(true);
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-tab-stage5")?.click(); });
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-workflow-products")?.click(); });
    expect(selected()).toBe("stage2");
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-workflow-reviews")?.click(); });
    expect(selected()).toBe("stage5");
    expect(host.querySelector<HTMLButtonElement>("#rrugc-tab-stage5")?.getAttribute("aria-selected")).toBe("true");
  });

  it("keeps stage arrow-key navigation inside the current group", async () => {
    await act(async () => {
      host.querySelector<HTMLElement>('.rrugc-stage-tabs')?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "ArrowLeft", cancelable: true }),
      );
    });
    expect(selected()).toBe("stage2");
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-workflow-reviews")?.click(); });
    await act(async () => {
      host.querySelector<HTMLElement>('.rrugc-stage-tabs')?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "End", cancelable: true }),
      );
    });
    expect(selected()).toBe("stage5");
    await act(async () => {
      host.querySelector<HTMLElement>('.rrugc-stage-tabs')?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "ArrowRight", cancelable: true }),
      );
    });
    expect(selected()).toBe("stage3");
  });
});
