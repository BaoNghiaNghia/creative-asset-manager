// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SearchIntelligenceModal } from "./SearchIntelligencePanel";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const summary = {
  total_queries: 213,
  cycles_completed: 15,
  new_keywords: 112,
  duplicate_pins: 25,
  active_leases: 1,
  lanes: [{ lane: "suggested", queries: 12, cycles: 7 }],
  recent: [{
    query: "funny trucker hats",
    lane: "suggested",
    cycles: 4,
    new_keywords: 17,
    scanned_pins: 32,
    duplicate_pins: 2,
    last_searched_at: "2026-10-09T05:00:00Z",
  }],
};

afterEach(() => {
  document.body.style.overflow = "";
  document.body.replaceChildren();
});

describe("Search Intelligence modal", () => {
  it("only mounts the dashboard when opened, exposes query drilldown and closes with Escape", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onClose = vi.fn();

    await act(async () => root.render(
      <SearchIntelligenceModal open={false} onClose={onClose} summary={summary} loading={false} error={null} />,
    ));
    expect(document.querySelector('[role="dialog"][aria-label="Search Intelligence"]')).toBeNull();

    await act(async () => root.render(
      <SearchIntelligenceModal open onClose={onClose} summary={summary} loading={false} error={null} />,
    ));
    const dialog = document.querySelector('[role="dialog"][aria-label="Search Intelligence"]')!;
    expect(dialog).not.toBeNull();
    expect(dialog.textContent).toContain("213");
    expect(dialog.textContent).toContain("112");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Close Search Intelligence");
    expect(dialog.querySelector(".rrugc-search-intelligence-expanded")).toBeNull();

    await act(async () => {
      (dialog.querySelector(".rrugc-search-intelligence-top button") as HTMLButtonElement).click();
    });
    expect(dialog.textContent).toContain("funny trucker hats");
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(onClose).toHaveBeenCalledTimes(1);

    await act(async () => root.render(
      <SearchIntelligenceModal open={false} onClose={onClose} summary={summary} loading={false} error={null} />,
    ));
    expect(document.body.style.overflow).toBe("");
    await act(async () => root.unmount());
  });
  it("closes on backdrop but not on modal content click", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onClose = vi.fn();
    await act(async () => root.render(
      <SearchIntelligenceModal open onClose={onClose} summary={summary} loading={false} error={null} />,
    ));
    const backdrop = document.querySelector(".rrugc-search-intelligence-backdrop") as HTMLDivElement;
    await act(async () => {
      backdrop.querySelector(".rrugc-search-intelligence")?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => backdrop.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });
});
