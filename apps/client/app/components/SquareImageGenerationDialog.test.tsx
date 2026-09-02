// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SquareImageGenerationDialog, generationStatusLabel } from "./SquareImageGenerationDialog";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const capabilities = {
  enabled: true,
  operations: ["square_expand"],
  target_sizes: [1024, 2048],
  providers: [
    { id: "cloudflare_sd", name: "Cloudflare SD (Free)", available: true, preservation_mode: "semantic_expand", recommended: true, model: "@cf/runwayml/stable-diffusion-v1-5-img2img" },
    { id: "adobe_firefly", name: "Adobe Firefly", available: true, preservation_mode: "strict_expand", recommended: false },
    { id: "gemini", name: "Gemini", available: true, preservation_mode: "semantic_expand", recommended: false, model: "gemini-3.1-flash-image" },
  ],
};

const queued = {
  id: "generation-1",
  source_asset_id: "asset-1",
  status: "queued",
  provider: "gemini",
  model: "gemini-3.1-flash-image",
  preservation_mode: "semantic_expand",
  target_width: 2048,
  target_height: 2048,
  output_asset_id: null,
  error: null,
};

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

async function mount(fetchMock: ReturnType<typeof vi.fn>, onOpenAsset = vi.fn()) {
  vi.stubGlobal("fetch", fetchMock);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<SquareImageGenerationDialog
      open
      assetId="asset-1"
      sourceName="beach.jpg"
      sourcePreviewUrl="/preview.jpg"
      onClose={() => undefined}
      onOpenAsset={onOpenAsset}
    />);
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return { host, root, onOpenAsset };
}

beforeEach(() => {
  sessionStorage.clear();
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    configurable: true,
    value: vi.fn(() => "00000000-0000-4000-8000-000000000020"),
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("Square image generation dialog", () => {
  it("shows source, provider semantics, size and optional prompt", async () => {
    const fetchMock = vi.fn(async () => response(capabilities));
    const { host, root } = await mount(fetchMock);
    expect(host.textContent).toContain("Generate square 1:1");
    expect(host.textContent).toContain("beach.jpg");
    expect(host.querySelector('img[alt="Source preview of beach.jpg"]')).not.toBeNull();
    expect(host.textContent).toContain("Strict preservation");
    expect(host.textContent).toContain("Semantic expansion");
    expect(host.querySelector('img[src="/brands/adobe-firefly.svg"]')).not.toBeNull();
    expect(host.querySelector(".gemini-logo svg")).not.toBeNull();
    expect(host.textContent).toContain("Recommended");
    expect(host.querySelector<HTMLInputElement>('input[value="cloudflare_sd"]')?.checked).toBe(true);
    expect(host.textContent).toContain("1024 x 1024");
    expect(host.textContent).toContain("2048 x 2048");
    expect(host.querySelector("textarea")).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("guards duplicate submit, polls completion, previews and opens result", async () => {
    vi.useFakeTimers();
    let resolveCreate!: (value: Response) => void;
    const createPromise = new Promise<Response>(resolve => { resolveCreate = resolve; });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/capabilities")) return Promise.resolve(response(capabilities));
      if (url.endsWith("/square")) return createPromise;
      if (url.endsWith("/generation-1")) return Promise.resolve(response({
        ...queued, status: "completed", output_asset_id: "output-1",
      }));
      throw Error("unexpected request " + url + " " + init?.method);
    });
    const { host, root, onOpenAsset } = await mount(fetchMock);
    const gemini = host.querySelector<HTMLInputElement>('input[value="gemini"]')!;
    const sizes = host.querySelectorAll<HTMLInputElement>('input[name="image-size"]');
    const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
    await act(async () => {
      gemini.click();
      sizes[1].click();
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(textarea, "keep the beach wider");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const generate = Array.from(host.querySelectorAll("button")).find(button => button.textContent === "Generate")!;
    await act(async () => { generate.click(); generate.click(); });
    expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/square"))).toHaveLength(1);
    await act(async () => resolveCreate(response(queued)));
    const createCall = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/square"))!;
    const sent = JSON.parse(String((createCall[1] as RequestInit).body));
    expect(sent.provider).toBe("gemini");
    expect(sent.target_size).toBe(2048);
    expect(sent.prompt).toBe("keep the beach wider");
    expect(sent.client_request_id).toBe("00000000-0000-4000-8000-000000000020");
    expect(host.textContent).toContain("Queued");

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(host.textContent).toContain("Completed");
    expect(host.querySelector('img[alt="Generated square result"]')?.getAttribute("src")).toContain("/generation-1/image");
    expect(host.querySelector('a[download]')).not.toBeNull();
    const open = Array.from(host.querySelectorAll("button")).find(button => button.textContent === "Open asset")!;
    await act(async () => open.click());
    expect(onOpenAsset).toHaveBeenCalledWith("output-1");
    await act(async () => root.unmount());
  });


  it("disables unavailable providers", async () => {
    const unavailable = {
      ...capabilities,
      enabled: true,
      providers: capabilities.providers.map(item => ({ ...item, available: false })),
    };
    const fetchMock = vi.fn(async () => response(unavailable));
    const { host, root } = await mount(fetchMock);
    expect(host.querySelectorAll('input[name="image-provider"]:disabled')).toHaveLength(3);
    expect(host.textContent).toContain("Unavailable");
    const generate = Array.from(host.querySelectorAll("button")).find(button => button.textContent === "Generate")!;
    expect(generate.disabled).toBe(true);
    await act(async () => root.unmount());
  });

  it("surfaces a restored terminal failure", async () => {
    sessionStorage.setItem("cam:image-generation:asset-1", "failed-generation");
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/capabilities")) return Promise.resolve(response(capabilities));
      return Promise.resolve(response({
        ...queued,
        id: "failed-generation",
        status: "failed",
        error: { code: "gemini_image_rate_limited", message: "Rate limit reached." },
      }));
    });
    const { host, root } = await mount(fetchMock);
    expect(host.textContent).toContain("Failed");
    expect(host.textContent).toContain("Rate limit reached.");
    expect(host.textContent).toContain("Try again");
    await act(async () => root.unmount());
  });

  it("cancels an active generation", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/capabilities")) return Promise.resolve(response(capabilities));
      if (url.endsWith("/square")) return Promise.resolve(response({ ...queued, provider: "adobe_firefly" }));
      if (url.endsWith("/cancel")) return Promise.resolve(response({ ...queued, status: "cancelled", provider: "adobe_firefly" }));
      throw Error("unexpected request");
    });
    const { host, root } = await mount(fetchMock);
    const generate = Array.from(host.querySelectorAll("button")).find(button => button.textContent === "Generate")!;
    await act(async () => generate.click());
    const cancel = Array.from(host.querySelectorAll("button")).find(button => button.textContent === "Cancel generation")!;
    await act(async () => cancel.click());
    expect(host.textContent).toContain("Cancelled");
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/cancel"))).toBe(true);
    await act(async () => root.unmount());
  });

  it("maps progress and terminal labels", () => {
    expect(generationStatusLabel("preparing")).toBe("Preparing");
    expect(generationStatusLabel("running")).toBe("Generating");
    expect(generationStatusLabel("storing")).toBe("Storing");
    expect(generationStatusLabel("failed")).toBe("Failed");
    expect(generationStatusLabel("cancelled")).toBe("Cancelled");
  });
});
