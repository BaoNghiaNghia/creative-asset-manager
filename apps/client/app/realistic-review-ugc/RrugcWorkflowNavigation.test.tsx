// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { RrugcWorkflowNavigation } from "./RrugcWorkflowNavigation";
import {
  RRUGC_WORKFLOW_GROUPS, rrugcStageFromSearch, rrugcWorkflowHref,
  stagesForWorkflowGroup, workflowGroupForStage, replaceRrugcStageInUrl,
  type RrugcStageTab,
} from "./workflowRouting";

describe("GatorHats sidebar groups and stage navigation", () => {
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
        <button type="button" id="open-reviews" onClick={() => setStage("stage3")}>Open reviews</button>
        <button type="button" id="open-products" onClick={() => setStage("stage0")}>Open products</button>
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

  it("maps only 0–2 to Products and 3–5 to Reviews", () => {
    expect(RRUGC_WORKFLOW_GROUPS.map(item => item.title)).toEqual(["GatorHats Products", "GatorHats Reviews"]);
    expect(stagesForWorkflowGroup("products").map(item => item.id)).toEqual(["stage0", "stage1", "stage2"]);
    expect(stagesForWorkflowGroup("reviews").map(item => item.id)).toEqual(["stage3", "stage4", "stage5"]);
    expect(workflowGroupForStage("stage5")).toBe("reviews");
    expect(tabs()).toEqual(["rrugc-tab-stage0", "rrugc-tab-stage1", "rrugc-tab-stage2"]);
    expect(host.querySelector("#rrugc-workflow-products")).toBeNull();
    expect(host.querySelector("#rrugc-workflow-reviews")).toBeNull();
  });

  it("switches stage sets without duplicating group navigation inside the page", async () => {
    await act(async () => { host.querySelector<HTMLButtonElement>("#rrugc-tab-stage2")?.click(); });
    expect(selected()).toBe("stage2");
    await act(async () => { host.querySelector<HTMLButtonElement>("#open-reviews")?.click(); });
    expect(selected()).toBe("stage3");
    expect(tabs()).toEqual(["rrugc-tab-stage3", "rrugc-tab-stage4", "rrugc-tab-stage5"]);
    expect(host.querySelector("#rrugc-tab-stage0")).toBeNull();
    expect(host.querySelector('.rrugc-workflow-group-tabs')).toBeNull();
  });

  it("keeps stage keyboard navigation within the visible workflow", async () => {
    await act(async () => {
      host.querySelector<HTMLElement>(".rrugc-stage-tabs")?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "ArrowLeft", cancelable: true }),
      );
    });
    expect(selected()).toBe("stage2");
    await act(async () => { host.querySelector<HTMLButtonElement>("#open-reviews")?.click(); });
    await act(async () => {
      host.querySelector<HTMLElement>(".rrugc-stage-tabs")?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "End", cancelable: true }),
      );
    });
    expect(selected()).toBe("stage5");
    await act(async () => {
      host.querySelector<HTMLElement>(".rrugc-stage-tabs")?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "ArrowRight", cancelable: true }),
      );
    });
    expect(selected()).toBe("stage3");
  });

  it("provides stable links, legacy deep links and safe validation of conflicting parameters", () => {
    expect(rrugcWorkflowHref("products")).toBe("/realistic-review-ugc?workflow=products&stage=stage0");
    expect(rrugcWorkflowHref("reviews")).toBe("/realistic-review-ugc?workflow=reviews&stage=stage3");
    expect(rrugcWorkflowHref("products", "stage5")).toBe(rrugcWorkflowHref("products"));
    expect(rrugcStageFromSearch("?workflow=reviews&stage=stage5")).toBe("stage5");
    expect(rrugcStageFromSearch("?workflow=products&stage=stage4")).toBe("stage0");
    expect(rrugcStageFromSearch("?stage=stage4")).toBe("stage4");
    expect(rrugcStageFromSearch("?workflow=invalid&stage=unknown")).toBe("stage0");
  });

  it("keeps one selected stage tab in each workflow", () => {
    expect(host.querySelectorAll('[role="tab"][aria-selected="true"]')).toHaveLength(1);
    expect(host.querySelectorAll('[role="tab"]')).toHaveLength(3);
  });

  it("updates workflow/stage in the URL while keeping unrelated query and hash", () => {
    window.history.replaceState(null, "", "/realistic-review-ugc?foo=bar#top");
    replaceRrugcStageInUrl("stage4");
    expect(window.location.search).toContain("foo=bar");
    expect(window.location.search).toContain("workflow=reviews");
    expect(window.location.search).toContain("stage=stage4");
    expect(window.location.hash).toBe("#top");
    window.history.replaceState(null, "", "/");
  });
});
