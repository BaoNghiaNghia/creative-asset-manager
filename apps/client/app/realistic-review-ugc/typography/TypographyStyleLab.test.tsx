// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TypographyStyleLab } from "./TypographyStyleLab";
import { getTypographyStyleLabOptions, previewTypographyStyle, listSavedTypographyV2, saveReviewedTypographyBatch, handoffTypographyV2, getCreativeV3Status, getCreativeV3Preview } from "./api";
import type { TypographyPreviewResponse, TypographyReference } from "./types";
import type { CreativeV3Preview } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", () => ({
  getTypographyStyleLabOptions: vi.fn(),
  previewTypographyStyle: vi.fn(),
  listSavedTypographyV2: vi.fn(),
  saveReviewedTypographyBatch: vi.fn(),
  handoffTypographyV2: vi.fn(),
  getCreativeV3Status: vi.fn(),
  getCreativeV3Preview: vi.fn(),
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
  vi.mocked(getTypographyStyleLabOptions).mockResolvedValue({
    keywords: [{ keyword_id: "kw-1", keyword: "Cà phê Việt Nam" }],
    references: [reference], approved_reference_count: 1, more_references: false,
    preview_only: true, provider_calls: 0,
  });
  vi.mocked(previewTypographyStyle).mockResolvedValue(preview);
  vi.mocked(getCreativeV3Status).mockResolvedValue({ available: false, schema_version: "creative-v3.0-preview-v1" });
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

async function renderLab(onBack = vi.fn(), active = true, selectedKeyword: { keyword_id: string; keyword: string } | null = { keyword_id: "kw-1", keyword: "Cà phê Việt Nam" }) {
  await act(async () => root.render(<TypographyStyleLab active={active}
    selectedKeyword={selectedKeyword}
    onBack={onBack} />));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return onBack;
}

async function expandAdvanced() {
  const details = host.querySelector<HTMLDetailsElement>(".typography-style-lab-advanced");
  await act(async () => {
    if (!details) throw new Error("Advanced tools unavailable");
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
    await Promise.resolve();
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("Style Lab G4 preview-only contract", () => {
  it("does not auto-preview a Stage 1 row that is not picked in Stage 0", async () => {
    await renderLab(vi.fn(), true, { keyword_id: "kw-unpicked", keyword: "UNPICKED" });
    expect(host.textContent).toContain("UNPICKED");
    expect(host.textContent).toContain("Not picked");
    expect(previewTypographyStyle).not.toHaveBeenCalled();
    expect(saveReviewedTypographyBatch).not.toHaveBeenCalled();
  });

  it("never bootstraps or previews while Style Lab is inactive", async () => {
    await renderLab(vi.fn(), false);
    expect(getTypographyStyleLabOptions).not.toHaveBeenCalled();
    expect(previewTypographyStyle).not.toHaveBeenCalled();
  });

  it("loads the newest picked keyword on direct Style Lab entry", async () => {
    await renderLab(vi.fn(), true, null);
    expect(host.querySelector<HTMLSelectElement>('select[aria-label="Picked keyword"]')?.value).toBe("kw-1");
    expect(getTypographyStyleLabOptions).toHaveBeenCalledWith(undefined, expect.any(AbortSignal));
    expect(previewTypographyStyle).toHaveBeenCalledTimes(1);
  });
  it("autopicks and previews approved G3 without any ImageGen or save action", async () => {
    const onBack = await renderLab();
    expect(host.textContent).toContain("Typography Auto Preview");
    expect(host.textContent).toContain("Cà phê Việt Nam");
    expect(host.textContent).toContain("No ImageGen");
    expect(host.querySelector(".typography-v2-mvp")).not.toBeNull();
    expect(host.querySelector(".typography-style-lab-advanced")?.getAttribute("open")).toBeNull();
    expect(getTypographyStyleLabOptions).toHaveBeenCalledWith("kw-1", expect.any(AbortSignal));
    expect(previewTypographyStyle).toHaveBeenCalledWith({
      reference_id: reference.id, keyword_id: "kw-1", count: 3,
    }, expect.any(AbortSignal));
    expect(previewTypographyStyle).toHaveBeenCalledTimes(1);
    expect(getCreativeV3Preview).not.toHaveBeenCalled();
    expect(saveReviewedTypographyBatch).not.toHaveBeenCalled();
    expect(handoffTypographyV2).not.toHaveBeenCalled();
    expect(host.querySelectorAll(".typography-style-lab-preview")).toHaveLength(3);
    expect(host.querySelectorAll(".typography-style-lab-image img")).toHaveLength(3);
    expect(host.querySelector(".typography-style-lab-image img")?.getAttribute("src")?.startsWith("data:image/svg+xml")).toBe(true);
    expect(host.textContent).toContain("Draft only");
    expect(host.querySelector("script")).toBeNull();
    const refresh = [...host.querySelectorAll("button")].find(item => item.textContent === "Regenerate previews");
    expect(refresh).toBeDefined();
    await act(async () => { refresh?.click(); await Promise.resolve(); });
    expect(previewTypographyStyle).toHaveBeenCalledTimes(2);
    const back = [...host.querySelectorAll("button")].find(item => item.textContent === "Back to Keyword Jobs");
    await act(async () => (back as HTMLButtonElement).click());
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("refuses unapproved references and does not make provider calls", async () => {
    vi.mocked(getTypographyStyleLabOptions).mockResolvedValueOnce({
      keywords: [{ keyword_id: "kw-1", keyword: "Cà phê Việt Nam" }],
      references: [], approved_reference_count: 1, more_references: false,
      preview_only: true, provider_calls: 0,
    });
    await renderLab();
    expect(host.textContent).toContain("No accepted G3 references");
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
describe("Creative Engine V3 read-only P1 bridge", () => {
  it("shows three distinct concepts only behind an explicit server canary", async () => {
    vi.mocked(getCreativeV3Status).mockResolvedValueOnce({
      available: true, schema_version: "creative-v3.0-preview-v1",
    });
    const quote = "Cà phê Việt Nam";
    const plan: CreativeV3Preview = {
      schema_version: "creative-v3.0-preview-v1",
      keyword_id: "kw-1",
      keyword_exact: quote,
      source_status: "completed",
      planner_status: "v1_bridge_preview_only",
      preview_only: true,
      job_created: false,
      can_generate_artwork: false,
      exact_text_locked: true,
      concepts: (["safe", "creative_twist", "explore"] as const).map((mode, i) => ({
        id: "concept-" + i, source_mode: mode, mode_label: mode,
        name: "Concept " + mode, description: "Original design direction",
        creative_hook: "Hook " + mode, meaning_rationale: "Identity",
        familiar_format: "Typography", twist_description: "One twist",
        typography_plan: "Readable", embroidery_plan: "Satin",
        clarity_assessment_unverified: "Not verified",
        embroidery_fit_unverified: "Not verified", risks: [],
        mechanism: null, mechanism_verified: false,
        text_spans: [{ text: quote, role: "locked_quote" }],
        design_blueprint_status: "not_composed",
        quality: { status: "needs_review", reasons: ["semantic_review_required"] },
      })),
    };
    vi.mocked(getCreativeV3Preview).mockResolvedValue(plan);
    await renderLab();
    expect(getCreativeV3Status).not.toHaveBeenCalled();
    await expandAdvanced();
    expect(getCreativeV3Status).toHaveBeenCalledOnce();
    expect(getCreativeV3Preview).toHaveBeenCalledWith("kw-1", expect.any(AbortSignal));
    expect(host.querySelectorAll(".typography-creative-v3-routes button")).toHaveLength(3);
    expect(host.textContent).toContain("Exact text locked");
    expect(host.textContent).toContain("Hook creative_twist");
    const explore = host.querySelector<HTMLButtonElement>(".typography-creative-v3-routes button:last-child");
    await act(async () => { explore?.click(); });
    expect(explore?.getAttribute("aria-pressed")).toBe("true");
    expect(host.textContent).toContain("Hook explore");
    expect(previewTypographyStyle).toHaveBeenCalledOnce();
    expect(saveReviewedTypographyBatch).not.toHaveBeenCalled();
    expect(handoffTypographyV2).not.toHaveBeenCalled();
  });

  it("does not invent a V3 plan when V1 is not analyzed", async () => {
    vi.mocked(getCreativeV3Status).mockResolvedValueOnce({
      available: true, schema_version: "creative-v3.0-preview-v1",
    });
    vi.mocked(getCreativeV3Preview).mockResolvedValue({
      schema_version: "creative-v3.0-preview-v1", keyword_id: "kw-1",
      keyword_exact: "Cà phê Việt Nam", source_status: "not_analyzed",
      planner_status: "v1_brief_not_ready",
      concepts: [], preview_only: true, job_created: false, can_generate_artwork: false,
    });
    await renderLab();
    await expandAdvanced();
    expect(host.textContent).toContain("Analyze this keyword first");
    expect(host.querySelectorAll(".typography-creative-v3-routes button")).toHaveLength(0);
    expect(previewTypographyStyle).toHaveBeenCalledOnce();
  });
});
