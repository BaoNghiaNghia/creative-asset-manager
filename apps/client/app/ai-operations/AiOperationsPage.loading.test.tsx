// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiOperationsPage } from "./AiOperationsPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json" },
  });
}

function normalResponse(url: string): Response {
  if (url === "/api/v1/auth/identity") return response({
    user_id: "user", active_tenant_id: "tenant", email: "operator@example.test",
    permissions: ["ai_operations.read"], roles: ["tenant_admin"],
  });
  if (url.endsWith("/configuration") || url.includes("/daily-sheet/status")) return response({ detail: "Test fixture unavailable" }, 503);
  if (url.includes("/summary?")) return response(null);
  if (url.includes("/jobs?") || url.includes("/usage?")) return response({ page: 1, page_size: 25, total: 0, items: [] });
  if (url.includes("/daily?") || url.includes("/providers?") || url.includes("/failures?")) return response({ items: [] });
  if (url.includes("/pipeline?")) return response({ detail: "Not Found" }, 404);
  if (url.includes("/media-dashboard?")) return response({});
  if (url.includes("/coverage/dashboard")) return response({ index_state: "available", totals: {}, ratios: {}, sources: [] });
  if (url.includes("/creative-pipeline/groups") || url.includes("/creative-pipeline/listings")) return response({ items: [], total: 0 });
  return response({});
}

async function mount(tab: string, fetcher: typeof fetch) {
  window.history.replaceState({}, "", `/ai-operations?tab=${tab}`);
  vi.stubGlobal("fetch", fetcher);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<AiOperationsPage />);
    await Promise.resolve();
  });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}

async function switchTo(tab: string) {
  const button = document.getElementById(`ops-tab-${tab}`) as HTMLButtonElement | null;
  expect(button).not.toBeNull();
  await act(async () => { button?.click(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("AI Operations live tab requests", () => {
  it.each([
    ["pipeline", ["pipeline", "media-dashboard", "summary"]],
    ["overview", ["summary", "daily", "providers", "failures"]],
    ["processing", ["jobs", "failures", "usage"]],
    ["cost", ["usage", "summary"]],
    ["visual-search", []],
    ["creative-pipeline", []],
    ["inventory", []],
    ["configuration", []],
  ])("loads only %s tab aggregates", async (tab, expected) => {
    const urls: string[] = [];
    await mount(tab as string, vi.fn(async input => {
      const url = String(input);
      urls.push(url);
      return normalResponse(url);
    }) as typeof fetch);
    const aggregatePaths = urls
      .filter(url => url.startsWith("/api/v1/admin/ai-operations/") && !url.endsWith("/configuration"))
      .map(url => url.split("/api/v1/admin/ai-operations/")[1].split("?")[0]);
    expect(new Set(aggregatePaths)).toEqual(new Set(expected as string[]));
    if (tab === "visual-search") expect(urls.some(url => url.includes("/coverage/dashboard"))).toBe(true);
    if (tab === "creative-pipeline") expect(urls.some(url => url.includes("/creative-pipeline/listings"))).toBe(true);
  });

  it("aborts the old tab, ignores its late response, and reuses a loaded tab", async () => {
    let resolveOld!: (value: Response) => void;
    let oldSignal: AbortSignal | undefined;
    let jobsCalls = 0;
    const urls: string[] = [];
    await mount("overview", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      urls.push(url);
      if (url.includes("/summary?")) {
        oldSignal = init?.signal as AbortSignal;
        return new Promise<Response>(resolve => { resolveOld = resolve; });
      }
      if (url.includes("/jobs?")) jobsCalls += 1;
      return Promise.resolve(normalResponse(url));
    }) as typeof fetch);
    await switchTo("processing");
    expect(oldSignal?.aborted).toBe(true);
    expect(jobsCalls).toBe(1);
    const processing = document.getElementById("ops-panel-processing");
    expect(processing?.textContent).toContain("processing jobs");
    await act(async () => { resolveOld(response({ completed: 999 })); });
    expect(document.getElementById("ops-panel-processing")?.textContent).not.toContain("999");
    await switchTo("visual-search");
    await switchTo("processing");
    expect(jobsCalls).toBe(1);
    expect(document.getElementById("ops-panel-processing")?.querySelector('[aria-busy="true"]')).toBeNull();
  });

  it("shows a secondary failure only on its own tab", async () => {
    await mount("processing", vi.fn(async input => {
      const url = String(input);
      return url.includes("/usage?")
        ? response({ detail: "Usage metrics unavailable" }, 503)
        : normalResponse(url);
    }) as typeof fetch);
    expect(container?.textContent).toContain("Usage metrics unavailable");
    await switchTo("overview");
    expect(container?.textContent).not.toContain("Usage metrics unavailable");
    await switchTo("processing");
    expect(container?.textContent).toContain("Usage metrics unavailable");
  });

  it("keeps an already visited Creative Pipeline tab mounted without refetching", async () => {
    const urls: string[] = [];
    await mount("creative-pipeline", vi.fn(async input => {
      const url = String(input);
      urls.push(url);
      return normalResponse(url);
    }) as typeof fetch);
    await switchTo("processing");
    await switchTo("creative-pipeline");
    expect(urls.filter(url => url.includes("/creative-pipeline/groups"))).toHaveLength(1);
    expect(urls.filter(url => url.includes("/creative-pipeline/listings"))).toHaveLength(1);
  });
});
