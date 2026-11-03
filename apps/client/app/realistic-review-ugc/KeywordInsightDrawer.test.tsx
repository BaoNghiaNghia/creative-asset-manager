// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { generateKeywordCreativeBrief, getKeywordIntelligence } from "./api";
import { KeywordInsightDrawer } from "./KeywordInsightDrawer";
import type { KeywordIntelligence, KeywordVolume } from "./types";

vi.mock("./api", () => ({
  getKeywordIntelligence: vi.fn(),
  generateKeywordCreativeBrief: vi.fn(),
}));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const item: KeywordVolume = {
  id: "keyword-123", keyword: "THIS IS MY COWBOY HAT", search_volume: 0,
  competition: null, cpc_low: null, cpc_high: null,
  source_image_url: null, source_pin_url: null,
  picked: false, picked_at: null, provider: "pending",
  fetched_at: "2026-11-03T00:00:00Z",
};
const report: KeywordIntelligence = {
  version: "keyword-intelligence-v2-preflight-1", keyword_id: item.id,
  keyword_exact: item.keyword, generated_at: "2026-11-03T00:00:00Z",
  brief_status: "not_analyzed", brief_updated_at: null,
  score: {
    value: null, coverage: 20, confidence: "low",
    recommendation: "needs_analysis", explanation: "Needs more analysis",
    is_preliminary: true,
    dimensions: [
      { key: "buyer", label: "Buyer & Emotion", weight: 25, score: null, source: "missing", note: "Needs brief" },
      { key: "embroidery", label: "Cap Embroidery Fit", weight: 20, score: 81, source: "text_preflight", note: "Length only" },
    ],
  },
  buyer: { audience: null, purchase_motivation: null, emotion: null, strategy: null, source: "not_analyzed" },
  creative: { hook: null, direction_count: 0, directions: [], originality_checked: false },
  embroidery: { word_count: 5, character_count: 21, preflight_score: 81, warnings: ["check cap"] , physical_fit_verified: false },
  evidence: { provider: "pending", volume: null, competition: null, source_pin_available: false, source_image_available: false, trademark_status: "unverified", trademark_legal_clearance: false },
  learning: { total_votes: 0, picked_votes: 0, rejected_votes: 0, rejected: [], general_rejections: [], scope: "tenant_and_exact_keyword" },
};

beforeEach(() => {
  vi.mocked(getKeywordIntelligence).mockReset();
  vi.mocked(getKeywordIntelligence).mockResolvedValue(report);
  vi.mocked(generateKeywordCreativeBrief).mockReset();
});
afterEach(() => { document.body.replaceChildren(); });

describe("Keyword Insight Drawer", () => {
  it("loads on demand, labels unavailable AI sections, and never queues without explicit action", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onPickChange = vi.fn();
    await act(async () => root.render(<KeywordInsightDrawer item={item}
      onClose={() => undefined} onPickChange={onPickChange} />));
    expect(getKeywordIntelligence).toHaveBeenCalledTimes(1);
    const drawer = document.body.querySelector(".rrugc-insight-drawer")!;
    expect(drawer).not.toBeNull();
    expect(drawer.textContent).toContain("OPPORTUNITY · PRELIMINARY");
    expect(drawer.textContent).toContain("Chưa đánh giá");
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-tabs button:nth-child(2)")?.click());
    expect(drawer.textContent).toContain("Chưa có Buyer Analysis");
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-tabs button:nth-child(4)")?.click());
    expect(drawer.textContent).toContain("Chưa có feedback");
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-btn.primary")?.click());
    expect(onPickChange).toHaveBeenCalledWith(item.id, true);
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("renders existing Creative Brief hypotheses and learning without a second AI call", async () => {
    const ready: KeywordIntelligence = {
      ...report, brief_status: "ready",
      score: { ...report.score, value: 78, coverage: 90, confidence: "medium", recommendation: "explore" },
      buyer: { audience: "Cowboy-hat fans", purchase_motivation: "Gift", emotion: "Humor", strategy: "Personal identity", source: "ai_hypothesis" },
      creative: { hook: "Letter interaction", direction_count: 9, originality_checked: false,
        directions: [{ mode: "safe", label: "Typography-first", name: "Western type", familiar_format: "Classic western", creative_twist: "Typography construction",
          creative_hook: "Readable western", variants: ["Western letters, readable in one line"] }] },
      learning: { ...report.learning, total_votes: 2, picked_votes: 1, rejected_votes: 1,
        rejected: [{ mode: "explore", variant: "B", count: 1, reason: "poor_cap_fit", guidance: "Improve fit" }] },
    };
    vi.mocked(getKeywordIntelligence).mockResolvedValue(ready);
    const root = createRoot(document.createElement("div"));
    const holder = document.createElement("div");
    document.body.append(holder);
    await act(async () => root.render(<KeywordInsightDrawer item={{ ...item, picked: true }}
      onClose={() => undefined} />));
    const drawer = document.body.querySelector(".rrugc-insight-drawer")!;
    expect(drawer.textContent).toContain("78");
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-tabs button:nth-child(2)")?.click());
    expect(drawer.textContent).toContain("Cowboy-hat fans");
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-tabs button:nth-child(3)")?.click());
    expect(drawer.textContent).toContain("Western type");
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-tabs button:nth-child(4)")?.click());
    expect(drawer.textContent).toContain("poor cap fit");
    await act(async () => drawer.querySelector<HTMLButtonElement>(".rrugc-insight-tabs button:nth-child(5)")?.click());
    expect(drawer.textContent).toContain("Chưa liên kết");
    expect(generateKeywordCreativeBrief).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });
});
