// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TypographyStyleLab } from "./TypographyStyleLab";
import { listTypographyReferences, previewTypographyStyle, listSavedTypographyV2, saveReviewedTypographyBatch, handoffTypographyV2 } from "./api";
import type { TypographyPreviewResponse, TypographyReference } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", () => ({
  listTypographyReferences: vi.fn(),
  previewTypographyStyle: vi.fn(),
  listSavedTypographyV2: vi.fn(),
  saveReviewedTypographyBatch: vi.fn(),
  handoffTypographyV2: vi.fn(),
}));

const reference: TypographyReference = {
  id: "ref-approved", asset_id: "asset-1", filename: "Arched Letters.png",
  content_hash: "a".repeat(64), source_type: "upload",
  source_url: null, source_platform: null, source_rights_status: "owned",
  source_attribution: null, privacy: "private", scope: "workspace",
  campaign_id: null, keyword_id: null, skill_name: null,
  reference_roles: ["layout", "font_mood", "overlap"], influence: "balanced",
  preference: "neutral", priority: 0, tags: [], notes: null,
  analysis_status: "ready", editorial_status: "approved", created_by_user_id: "owner",
  created_at: "2026-10-29T00:00:00Z", updated_at: "2026-10-29T00:00:00Z",
  image_url: "/api/v1/realistic-review-ugc/typography/references/ref-approved/image",
};

const preview: TypographyPreviewResponse = {
  reference_id: reference.id, keyword_id: "kw-1", source_keyword_exact: "Cà phê Việt Nam",
  preview_only: true, job_created: false, provider_calls: 0,
  items: ["arched", "center_stack", "offset_stack"].map(layout => ({
    id: layout, preview_version: "g4-balanced-svg-mvp-v1",
    source_keyword_exact: "Cà phê Việt Nam", reference_id: reference.id, dna_id: "dna-1",
    layout, font_family: "DejaVu Serif", influence: "balanced" as const,
    style_components: ["layout", "font_mood", "overlap"],
    svg: '<svg xmlns="http://www.w3.org/2000/svg"><text>Cà phê Việt Nam</text></svg>',
    transparent_background: true, preview_only: true as const, eligible_for_generation: false as const,
    qa: { exact_keyword: true, status: "preview_only" as const, requires_human_review: true },
  })),
};

let host: HTMLElement, root: Root;
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.mocked(listTypographyReferences).mockResolvedValue({
    items: [reference], total: 1, limit: 100, offset: 0,
    stats: { total: 1, approved: 1, needs_review: 0, analyzing: 0 },
    capabilities: { read: true, upload: true, manage: true },
  });
  vi.mocked(previewTypographyStyle).mockResolvedValue(preview);
  vi.mocked(listSavedTypographyV2).mockResolvedValue({ keyword_id: "kw-1", items: [] });
  vi.mocked(saveReviewedTypographyBatch).mockResolvedValue({
    saved_count: 1, new_count: 1, items: [{ slot: 1, version: 1 }],
    published: false, stage2_jobs_created: 0,
  });
  vi.mocked(handoffTypographyV2).mockResolvedValue({
    selected: true, source_plan_id: "source-1", scout_started: false,
    stage2_jobs_created: 0,
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove(); document.body.replaceChildren();
  vi.clearAllMocks();
});

async function renderLab(onBack = vi.fn(), active = true) {
  await act(async () => root.render(<TypographyStyleLab active={active}
    keywords={[{ keyword_id: "kw-1", keyword: "Cà phê Việt Nam" }]}
    selectedKeyword={{ keyword_id: "kw-1", keyword: "Cà phê Việt Nam" }}
    onBack={onBack} />));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return onBack;
}

describe("Style Lab G4 preview-only contract", () => {
  it("previews only on explicit click and never launches a generation job", async () => {
    const onBack = await renderLab();
    expect(host.textContent).toContain("Balanced Typography");
    expect(host.textContent).toContain("Cà phê Việt Nam");
    expect(host.textContent).toContain("0 ImageGen calls");
    expect(host.textContent).toContain("Typography V2 · Reviewed output workflow");
    expect(host.querySelector(".typography-v2-mvp")).not.toBeNull();
    expect(host.querySelector(".typography-v2-mvp")?.parentElement?.getAttribute("data-testid"))
      .toBe("typography-style-lab");
    expect(listTypographyReferences).toHaveBeenCalledWith({ status: "approved", limit: 100 }, expect.any(AbortSignal));
    expect(previewTypographyStyle).not.toHaveBeenCalled();
    const button = [...host.querySelectorAll("button")].find(item => item.textContent === "Preview 3 concepts");
    expect(button?.hasAttribute("disabled")).toBe(false);
    await act(async () => (button as HTMLButtonElement).click());
    expect(previewTypographyStyle).toHaveBeenCalledWith({
      reference_id: reference.id, keyword_id: "kw-1", count: 3,
    }, expect.any(AbortSignal));
    expect(host.querySelectorAll(".typography-style-lab-preview")).toHaveLength(3);
    expect(host.querySelectorAll(".typography-style-lab-image img")).toHaveLength(3);
    expect(host.querySelector(".typography-style-lab-image img")?.getAttribute("src")?.startsWith("data:image/svg+xml")).toBe(true);
    expect(host.textContent).toContain("Draft only");
    expect(host.querySelector("script")).toBeNull();
    const back = [...host.querySelectorAll("button")].find(item => item.textContent === "Back to Keyword Jobs");
    await act(async () => (back as HTMLButtonElement).click());
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("refuses unapproved references and does not make provider calls", async () => {
    vi.mocked(listTypographyReferences).mockResolvedValueOnce({
      items: [{ ...reference, editorial_status: "candidate", source_rights_status: "unknown" }],
      total: 1, limit: 100, offset: 0,
      stats: { total: 1, approved: 0, needs_review: 1, analyzing: 0 },
      capabilities: { read: true, upload: true, manage: true },
    });
    await renderLab();
    expect(host.textContent).toContain("No approved references");
    const button = [...host.querySelectorAll("button")].find(item => item.textContent === "Preview 3 concepts");
    expect(button?.hasAttribute("disabled")).toBe(true);
    expect(previewTypographyStyle).not.toHaveBeenCalled();
  });
});

describe("Reviewed V2 MVP actions are explicitly opted in", () => {
  it("does not save or select Stage 2 on initial render", async () => {
    await renderLab();
    expect(saveReviewedTypographyBatch).not.toHaveBeenCalled();
    expect(handoffTypographyV2).not.toHaveBeenCalled();
  });
});
