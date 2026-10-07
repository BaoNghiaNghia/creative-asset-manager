// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RrugcSmartSearchInput } from "./RrugcSmartSearchInput";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  identity: vi.fn(),
}));
vi.mock("../../features/access_management", () => ({ fetchAccessIdentity: mocks.identity }));

let host: HTMLDivElement;
let root: Root;

function Harness({ stageId = "stage2" }: { stageId?: string }) {
  const [query, setQuery] = useState("");
  return <RrugcSmartSearchInput
    stageId={stageId}
    query={query}
    onQueryChange={setQuery}
    placeholder="Search groups…"
    label="Search groups"
    suggestions={[
      { value: "Houston Astros", meta: "Sports", badge: "Group" },
      { value: "Blue Houston Cap", meta: "Folder", badge: "Folder" },
    ]}
  />;
}

async function mount(stageId = "stage2") {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<Harness stageId={stageId} />);
    await Promise.resolve();
  });
  return host.querySelector<HTMLInputElement>("#rrugc-" + stageId + "-search-input")!;
}

function inputValue(input: HTMLInputElement, next: string) {
  const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  native.call(input, next);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  window.localStorage.clear();
  mocks.identity.mockReset();
  mocks.identity.mockResolvedValue({ active_tenant_id: "tenant-a", user_id: "user-a" });
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove();
  document.body.replaceChildren();
});

describe("shared RRUGC smart search", () => {
  it("shows search completion, recommendations, and persists recent searches per stage", async () => {
    const input = await mount();
    await act(async () => inputValue(input, "hou"));
    await act(async () => input.focus());

    expect(host.textContent).toContain("Search completion");
    expect(host.textContent).toContain("Houston Astros");
    expect(host.textContent).toContain("Recommended");
    expect(host.textContent).toContain("Blue Houston Cap");
    expect(host.textContent).toContain("Search this term");

    const completion = Array.from(host.querySelectorAll<HTMLButtonElement>('[role="option"]'))
      .find(button => button.textContent?.includes("Houston Astros"))!;
    await act(async () => completion.click());

    expect(input.value).toBe("Houston Astros");
    const key = "creative-asset-manager:rrugc-stage-search-history:v1:stage2:tenant-a:user-a";
    expect(JSON.parse(window.localStorage.getItem(key)!)).toEqual(["Houston Astros"]);

    await act(async () => input.click());
    expect(host.textContent).toContain("Recent searches");
    expect(host.textContent).toContain("Houston Astros");
  });

  it("keeps search history isolated between stages", async () => {
    const stage2Key = "creative-asset-manager:rrugc-stage-search-history:v1:stage2:tenant-a:user-a";
    const stage3Key = "creative-asset-manager:rrugc-stage-search-history:v1:stage3:tenant-a:user-a";
    window.localStorage.setItem(stage2Key, JSON.stringify(["Stage 2 only"]));
    window.localStorage.setItem(stage3Key, JSON.stringify(["Stage 3 only"]));

    const input = await mount("stage3");
    await act(async () => input.focus());

    expect(host.textContent).toContain("Stage 3 only");
    expect(host.textContent).not.toContain("Stage 2 only");
  });
});
