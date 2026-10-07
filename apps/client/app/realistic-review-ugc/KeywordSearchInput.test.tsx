// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KeywordSearchInput } from "./KeywordSearchInput";
import { keywordHistoryKey } from "./keywordSearchHistory";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const mocks = vi.hoisted(() => ({
  identity: vi.fn(),
  suggestions: vi.fn(),
}));
vi.mock("../../features/access_management", () => ({ fetchAccessIdentity: mocks.identity }));
vi.mock("./api", () => ({ suggestKeywordAnalysis: mocks.suggestions }));

let host: HTMLDivElement;
let root: Root;
function Harness() {
  const [query, setQuery] = useState("");
  return <KeywordSearchInput query={query} onQueryChange={setQuery} />;
}
async function mount() {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<Harness />);
    await Promise.resolve();
  });
  return host.querySelector<HTMLInputElement>("#rrugc-stage0-search-input")!;
}
function inputValue(input: HTMLInputElement, next: string) {
  const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  native.call(input, next);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  window.localStorage.clear();
  vi.useFakeTimers();
  mocks.identity.mockReset();
  mocks.suggestions.mockReset();
  mocks.identity.mockResolvedValue({ active_tenant_id: "tenant-a", user_id: "user-a" });
  mocks.suggestions.mockResolvedValue([
    { keyword: "Houston Astros", search_volume: 4400, favorite: true, picked: false },
    { keyword: "Blue Houston Cap", search_volume: 200, favorite: false, picked: false },
  ]);
});
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove();
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe("Stage 0 keyword autocomplete", () => {
  it("fetches tenant-scoped recommendations after debounce and stores selected searches", async () => {
    const key = keywordHistoryKey("tenant-a", "user-a")!;
    window.localStorage.setItem(key, JSON.stringify(["Old Houston Hat"]));
    const input = await mount();
    await act(async () => input.focus());
    expect(host.textContent).toContain("Recent searches");
    expect(host.textContent).toContain("Old Houston Hat");

    await act(async () => inputValue(input, "hou"));
    expect(mocks.suggestions).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(mocks.suggestions).toHaveBeenCalledWith("hou", expect.any(AbortSignal));
    expect(host.textContent).toContain("Keyword recommendations");
    const recommendation = Array.from(host.querySelectorAll<HTMLButtonElement>('[role="option"]'))
      .find(item => item.textContent?.includes("Houston Astros"))!;
    await act(async () => recommendation.click());
    expect(input.value).toBe("Houston Astros");
    expect(JSON.parse(window.localStorage.getItem(key)!)).toEqual(["Houston Astros", "Old Houston Hat"]);

    await act(async () => input.click());
    const clear = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find(item => item.textContent === "Clear history")!;
    await act(async () => clear.click());
    expect(window.localStorage.getItem(key)).toBeNull();
  });

  it("supports keyboard history selection and per-item removal without leaking other users", async () => {
    const key = keywordHistoryKey("tenant-a", "user-a")!;
    const foreign = keywordHistoryKey("tenant-b", "user-a")!;
    window.localStorage.setItem(key, JSON.stringify(["Favorite Hat", "Second Hat"]));
    window.localStorage.setItem(foreign, JSON.stringify(["Other Tenant Secret"]));
    const input = await mount();
    await act(async () => input.focus());
    expect(host.textContent).not.toContain("Other Tenant Secret");
    expect(host.querySelectorAll('[role="option"]')).toHaveLength(2);
    await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(input.value).toBe("Favorite Hat");
    expect(JSON.parse(window.localStorage.getItem(key)!)[0]).toBe("Favorite Hat");

    await act(async () => input.click());
    const remove = host.querySelector<HTMLButtonElement>('[aria-label="Remove from search history: Favorite Hat"]')!;
    await act(async () => remove.click());
    expect(JSON.parse(window.localStorage.getItem(key)!)).toEqual(["Second Hat"]);
    expect(window.localStorage.getItem(foreign)).toBe(JSON.stringify(["Other Tenant Secret"]));
  });
});
