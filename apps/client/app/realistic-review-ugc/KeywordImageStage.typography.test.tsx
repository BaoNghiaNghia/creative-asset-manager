// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KeywordImageStage } from "./KeywordImageStage";
import { getTypographyStatus, listTypographyReferences } from "./typography/api";
import type { TypographyReferencePage } from "./typography/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async original => ({
  ...(await original<typeof import("./api")>()),
  listStage2Skills: vi.fn(async () => ({
    stage_defaults: { stage1: "local:concept-short-v1" },
    items: [{
      source: "local", skill_id: null, skill_name: "concept-short-v1",
      display_name: "Concept Short v1", ready: true, keyword_artwork_ready: true,
      default_version: null, synced_version: null, local_version: null,
      version_options: [], sync_state: "ready",
    }],
  })),
  listKeywordImages: vi.fn(async () => ({
    total: 1, page: 1, page_size: 20,
    overview: { not_run: 1, queued: 0, running: 0, completed: 0, failed: 0 },
    items: [{
      keyword_id: "kw-a", keyword: "BEACH BABE", search_volume: 40,
      source_image_url: null, status: "not_run", job_id: null,
      skill_name: null, skill_version: null, retry_count: 0,
      saved_output_count: 0, attempt_count: 0, max_attempts: 0,
      error_code: null, error_message: null, output_url: null, updated_at: null,
    }],
  })),
}));
vi.mock("./typography/api", async original => ({
  ...(await original<typeof import("./typography/api")>()),
  getTypographyStatus: vi.fn(),
  listTypographyReferences: vi.fn(),
}));
const page: TypographyReferencePage = {
  items: [], total: 0, offset: 0, limit: 1, stats: {
    total: 3, approved: 2, needs_review: 1, analyzing: 0,
  }, capabilities: { read: true, upload: true, manage: true },
};
let host: HTMLElement, root: Root;
beforeEach(() => {
  window.history.replaceState({}, "", "/realistic-review-ugc?stage=1");
  host = document.createElement("div");
  document.body.append(host); root = createRoot(host);
  vi.mocked(getTypographyStatus).mockResolvedValue({ available: true });
  vi.mocked(listTypographyReferences).mockResolvedValue(page);
});
afterEach(async () => {
  vi.unstubAllEnvs();
  await act(async () => root.unmount());
  host.remove(); document.body.replaceChildren();
  vi.clearAllMocks();
});

async function renderStage() {
  await act(async () => root.render(
    <KeywordImageStage active skillCatalogRevision={0} onManageSkills={() => undefined} />,
  ));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}
async function click(button: Element | null) {
  expect(button).not.toBeNull();
  await act(async () => (button as HTMLElement).click());
}

describe("Stage 1 Typography V2 feature gating and non-destructive navigation", () => {
  it("shows the MVP entry and probes the already-deployed V2 API in production", async () => {
    vi.stubEnv("PROD", true);
    vi.stubEnv("VITE_TYPOGRAPHY_V2_STATUS_PROBE", "");
    await renderStage();
    expect(getTypographyStatus).toHaveBeenCalledOnce();
    expect(listTypographyReferences).toHaveBeenCalledOnce();
    expect(host.querySelector("[data-testid=typography-stage1-subnav]")).not.toBeNull();
    expect(host.querySelector("[data-testid=typography-v2-entry]")?.textContent).toContain("Typography V2");
    expect(host.querySelector(".rrugc-keyword-gen-table")?.textContent).toContain("Picked date");
  });

  it("shows a clear unavailable state in production without calling private V2 endpoints", async () => {
    vi.stubEnv("PROD", true);
    vi.mocked(getTypographyStatus).mockResolvedValueOnce({ available: false });
    await renderStage();
    expect(getTypographyStatus).toHaveBeenCalledOnce();
    expect(listTypographyReferences).not.toHaveBeenCalled();
    expect(host.querySelector("[data-testid=typography-stage1-subnav]")).not.toBeNull();
    await click(host.querySelector("[data-testid=typography-v2-entry]"));
    expect(host.querySelector("[data-testid=typography-v2-availability]")?.textContent)
      .toContain("not enabled for this workspace");
    expect(host.querySelector("[data-testid=typography-style-lab]")).toBeNull();
  });

  it("keeps V1 keyword jobs unchanged when the V2 backend is OFF or forbidden", async () => {
    vi.mocked(getTypographyStatus).mockResolvedValueOnce({ available: false });
    await renderStage();
    expect(host.querySelector("[data-testid=typography-stage1-subnav]")).not.toBeNull();
    expect(host.querySelector(".rrugc-keyword-gen-table")).not.toBeNull();
    expect(host.textContent).toContain("BEACH BABE");
    expect(host.textContent).toContain("Generate all unused");
    expect(host.textContent).not.toContain("Style Lab · Later");
  });

  it("switches only inside Stage 1 and retains jobs table state", async () => {
    await renderStage();
    const nav = host.querySelector("[data-testid=typography-stage1-subnav]");
    expect(nav).not.toBeNull();
    expect(nav?.textContent).toContain("Typography Ideas 3");
    const table = host.querySelector(".rrugc-keyword-gen-table");
    expect(table?.textContent).toContain("BEACH BABE");
    const input = host.querySelector<HTMLInputElement>(".rrugc-keyword-gen-search input");
    expect(input).not.toBeNull();
    await click(nav?.querySelectorAll("button")[1] ?? null);
    expect(host.textContent).toContain("Typography Ideas Library");
    expect(host.querySelector(".typography-keyword-jobs-shell")?.hasAttribute("hidden")).toBe(true);
    expect(host.querySelector(".rrugc-keyword-gen-table")).toBe(table);
    expect(new URLSearchParams(window.location.search).get("view")).toBe("ideas");
    expect(host.textContent).not.toContain("Generate eligible");
    await click(nav?.querySelectorAll("button")[0] ?? null);
    expect(host.querySelector(".typography-keyword-jobs-shell")?.hasAttribute("hidden")).toBe(false);
    expect(host.querySelector(".rrugc-keyword-gen-table")).toBe(table);
    expect(host.textContent).toContain("Generate all unused");
    expect(new URLSearchParams(window.location.search).has("view")).toBe(false);
  });

  it("opens a selected picked keyword in preview-only Style Lab without losing the V1 table", async () => {
    await renderStage();
    const table = host.querySelector(".rrugc-keyword-gen-table");
    const apply = host.querySelector<HTMLButtonElement>('button[aria-label="Apply style to BEACH BABE"]');
    expect(apply).not.toBeNull();
    await click(apply);
    expect(host.querySelector("[data-testid=typography-style-lab]")).not.toBeNull();
    expect(host.textContent).toContain("BEACH BABE");
    expect(host.textContent).toContain("0 ImageGen calls");
    expect(host.querySelector(".typography-keyword-jobs-shell")?.hasAttribute("hidden")).toBe(true);
    expect(host.querySelector(".rrugc-keyword-gen-table")).toBe(table);
    expect(new URLSearchParams(window.location.search).get("view")).toBe("style-lab");
    const back = [...host.querySelectorAll("button")].find(item => item.textContent === "Back to Keyword Jobs");
    await click(back || null);
    expect(host.querySelector(".typography-keyword-jobs-shell")?.hasAttribute("hidden")).toBe(false);
    expect(host.querySelector(".rrugc-keyword-gen-table")).toBe(table);
  });
});
