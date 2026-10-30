// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreativeBriefDialog } from "./CreativeBriefDialog";
import { generateKeywordCreativeBrief, getKeywordCreativeBrief, getCreativeV3GenerationStatus, generateCreativeV3Stage1Images } from "./api";
import type { KeywordCreativeBriefDetail } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async importOriginal => ({
  ...(await importOriginal<typeof import("./api")>()),
  getKeywordCreativeBrief: vi.fn(),
  generateKeywordCreativeBrief: vi.fn(),
  getCreativeV3GenerationStatus: vi.fn(),
  generateCreativeV3Stage1Images: vi.fn(),
}));

const keyword = "CREATIVE DICTATOR";
const keywordId = "kw-1";

const makeDetail = (
  status: KeywordCreativeBriefDetail["status"],
  withBrief = false,
): KeywordCreativeBriefDetail => ({
  keyword_id: keywordId,
  keyword,
  status,
  prompt_version: "meaning-first-v1",
  updated_at: "2026-10-30T10:00:00Z",
  error_code: status === "failed" ? "temporary_provider_error" : null,
  brief: withBrief ? {
    keyword_exact: keyword,
    strategy: "Playful authority",
    meaning_emotion: "Confident and humorous",
    target_audience: "Creative workers",
    purchase_motivation: "An expressive gift",
    creative_hook: "The letters take command of the composition",
    concepts: ([
      { mode: "safe", name: "Strong Type" },
      { mode: "creative_twist", name: "Creative Interruption" },
      { mode: "explore", name: "Broken Grid" },
    ] as const).map(({ mode, name }) => ({
      mode, name,
      familiar_format: "Classic bold type " + mode,
      creative_twist: "Interlocking lines " + mode,
      description: "Description for " + name,
      creative_hook: "Hook for " + name,
      typography_plan: "Typography for " + name,
      embroidery_plan: "Embroidery for " + name,
      clarity_assessment: "Readable in 2 seconds",
      embroidery_fit: "Feasible on a crown",
      risks: mode === "explore" ? ["Avoid fine lines"] : [],
    })),
  } : null,
});

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let onClose = vi.fn();

async function renderDialog(detail: KeywordCreativeBriefDetail) {
  vi.mocked(getKeywordCreativeBrief).mockResolvedValue(detail);
  host = document.createElement("div");
  const opener = document.createElement("button");
  opener.textContent = "Open";
  document.body.append(opener, host);
  opener.focus();
  root = createRoot(host);
  await act(async () => root?.render(
    <CreativeBriefDialog keywordId={keywordId} keyword={keyword} onClose={onClose} />,
  ));
  await act(async () => { await Promise.resolve(); });
  return opener;
}

beforeEach(() => {
  vi.clearAllMocks();
  onClose = vi.fn();
  vi.mocked(generateKeywordCreativeBrief).mockResolvedValue({} as never);
  vi.mocked(getCreativeV3GenerationStatus).mockResolvedValue({
    available: false, schema_version: "creative-v3/stage1-six-v1", output_count: 6,
  });
  vi.mocked(generateCreativeV3Stage1Images).mockResolvedValue({
    keyword_id: keywordId, job_id: "job-1", status: "queued",
    queued: true, expected_output_count: 6,
  });
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host = undefined;
  document.body.replaceChildren();
  document.body.style.overflow = "";
});

describe("Creative Brief V1 modal redesign", () => {
  it("shows an actionable empty state with the 3-step outline and keeps Stage 1 outputs safe", async () => {
    await renderDialog(makeDetail("not_analyzed"));
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("CREATIVE DICTATOR");
    expect(dialog?.textContent).toContain("A stronger idea starts with the meaning.");
    expect(dialog?.textContent).toContain("3 distinct concept routes");
    expect(dialog?.textContent).toContain("Stage 1 images and Output History remain unchanged.");
    expect(document.querySelectorAll(".rrugc-creative-brief-preview-item")).toHaveLength(3);
    await act(async () => {
      (document.querySelector(".rrugc-creative-brief-primary") as HTMLButtonElement).click();
      await Promise.resolve();
    });
    expect(generateKeywordCreativeBrief).toHaveBeenCalledTimes(1);
    expect(generateKeywordCreativeBrief).toHaveBeenCalledWith(keywordId);
  });

  it("shows an indeterminate progress state without allowing duplicate analysis", async () => {
    await renderDialog(makeDetail("running"));
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Turning meaning into creative directions.");
    expect(dialog?.textContent).toContain("Codex is analyzing the keyword");
    expect(dialog?.querySelector(".rrugc-creative-brief-primary")).toBeNull();
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
  });

  it("shows retry on a failed brief and does not hide the three-step explanation", async () => {
    await renderDialog(makeDetail("failed"));
    expect(document.querySelector(".rrugc-creative-brief-status")?.textContent).toContain("Needs retry");
    expect(document.querySelector(".rrugc-creative-brief-primary")?.textContent).toContain("Retry analysis");
    expect(document.querySelectorAll(".rrugc-creative-brief-preview-item")).toHaveLength(3);
    await act(async () => {
      (document.querySelector(".rrugc-creative-brief-primary") as HTMLButtonElement).click();
      await Promise.resolve();
    });
    expect(generateKeywordCreativeBrief).toHaveBeenCalledOnce();
  });

  it("shows strategy, 3 selectable concepts, risk and selected execution detail", async () => {
    await renderDialog(makeDetail("completed", true));
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Playful authority");
    expect(dialog?.textContent).toContain("Confident and humorous");
    expect(dialog?.querySelectorAll(".rrugc-creative-brief-proposal")).toHaveLength(3);
    expect(dialog?.textContent).toContain("Embroidery for Creative Interruption");
    expect(dialog?.textContent).not.toContain("Avoid fine lines");
    const explore = dialog?.querySelector<HTMLButtonElement>('button[aria-label="Explore: Broken Grid"]');
    await act(async () => { explore?.click(); });
    expect(explore?.getAttribute("aria-pressed")).toBe("true");
    expect(dialog?.textContent).toContain("Embroidery for Broken Grid");
    expect(dialog?.textContent).toContain("Avoid fine lines");
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
  });

  it("traps keyboard focus, closes on Escape and restores the former focus", async () => {
    const opener = await renderDialog(makeDetail("not_analyzed"));
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    expect(document.activeElement).toBe(dialog);
    await act(async () => {
      dialog?.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    });
    expect(document.activeElement).toBe(dialog?.querySelector(".rrugc-creative-brief-close"));
    await act(async () => {
      dialog?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledOnce();
    await act(async () => root?.unmount());
    root = undefined;
    expect(document.activeElement).toBe(opener);
    expect(document.body.style.overflow).toBe("");
  });

  it("does not expose ImageGen unless the tenant is explicitly enabled", async () => {
    await renderDialog(makeDetail("completed", true));
    expect(document.querySelector(".rrugc-creative-v3-generate")).toBeNull();
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
  });

  it("queues all six V1-grounded embroidery slots only on a direct user click", async () => {
    vi.mocked(getCreativeV3GenerationStatus).mockResolvedValueOnce({
      available: true, schema_version: "creative-v3/stage1-six-v1", output_count: 6,
    });
    await renderDialog(makeDetail("completed", true));
    const button = document.querySelector<HTMLButtonElement>(".rrugc-creative-v3-generate button");
    expect(button?.textContent).toContain("Generate 6 embroidery designs");
    expect(generateCreativeV3Stage1Images).not.toHaveBeenCalled();
    await act(async () => { button?.click(); await Promise.resolve(); });
    expect(generateCreativeV3Stage1Images).toHaveBeenCalledOnce();
    expect(generateCreativeV3Stage1Images).toHaveBeenCalledWith(keywordId, "2026-10-30T10:00:00Z");
    expect(document.querySelector(".rrugc-creative-v3-generate")?.textContent).toContain("6 images queued");
    expect(button?.disabled).toBe(true);
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
  });

});