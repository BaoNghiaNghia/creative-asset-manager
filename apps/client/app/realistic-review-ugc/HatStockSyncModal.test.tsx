// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { HatStockSyncModal } from "./HatStockSyncModal";

const api = vi.hoisted(() => ({
  preview: vi.fn(),
  apply: vi.fn(),
}));
vi.mock("./api", () => ({
  previewHatStockZip: api.preview,
  applyHatStockZip: api.apply,
}));

const sample = {
  sha256: "a".repeat(64),
  ready_count: 1,
  file_count: 5,
  groups: [
    { color: "natural-black", ready: true, views: {
      front: ["Natural Black Front.jpg"], side: ["Natural Black Side.jpg"], back: ["Natural Black Back.jpg"],
    }, duplicates: [], missing: [] },
    { color: "natural-navy", ready: false, views: {
      front: ["Natural Navy Front.jpg"], back: ["Natural Navy Back.jpg"],
    }, missing: ["side"], duplicates: [] },
  ],
  unmatched: [],
};

describe("Hat stock ZIP preview and import", () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    api.preview.mockResolvedValue(sample);
    api.apply.mockResolvedValue({ ...sample, applied_count: 1, applied: ["natural-black"] });
  });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    host.remove();
    vi.clearAllMocks();
  });

  it("previews complete/partial named groups before sending the unchanged ZIP to apply", async () => {
    const onApplied = vi.fn();
    await act(async () => { root.render(<HatStockSyncModal onClose={() => undefined} onApplied={onApplied} />); });
    const buttons = () => [...host.querySelectorAll<HTMLButtonElement>("button")];
    expect(buttons().find(button => button.textContent?.includes("Sync ready color groups"))?.disabled).toBe(true);
    const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["sample zip contents"], "8869.zip", { type: "application/zip" });
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => { buttons().find(button => button.textContent?.includes("Preview groups"))?.click(); });
    expect(api.preview).toHaveBeenCalledWith(file);
    expect(host.textContent).toContain("natural black");
    expect(host.textContent).toContain("Missing: side");
    await act(async () => { buttons().find(button => button.textContent?.includes("Sync ready color groups"))?.click(); });
    expect(api.apply).toHaveBeenCalledWith(file, sample.sha256);
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("Synced 1 color groups");
  });
});
