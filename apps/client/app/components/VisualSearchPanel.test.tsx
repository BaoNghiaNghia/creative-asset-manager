// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VisualSearchPanel, isVisualSearchImageFile } from "./VisualSearchPanel";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function props(onUpload = vi.fn()) {
  return {
    scope: "all" as const,
    canSearchAllResources: true,
    onScopeChange: () => undefined,
    hasCurrentSource: false,
    hasCurrentFolder: false,
    reference: null,
    loading: false,
    error: "",
    refinement: "",
    onRefinementChange: () => undefined,
    onUpload,
    onApplyCrop: () => undefined,
    onRetry: () => undefined,
    onClose: () => undefined,
  };
}

afterEach(() => {
  document.body.replaceChildren();
});

describe("VisualSearchPanel upload picker", () => {
  it.each([
    ["reference.JFIF", ""],
    ["iphone-photo.heic", "application/octet-stream"],
  ])("accepts %s when Windows does not provide an image MIME type", async (name, type) => {
    const onUpload = vi.fn();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<VisualSearchPanel {...props(onUpload)} />);
    });

    const input = host.querySelector<HTMLInputElement>(".visual-search-file-input")!;
    const file = new File(["image-bytes"], name, { type });
    Object.defineProperty(input, "files", { configurable: true, value: [file] });

    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(onUpload).toHaveBeenCalledWith(file);

    await act(async () => root.unmount());
  });

  it("uses a native label-to-input picker and resets the input before every selection", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<VisualSearchPanel {...props()} />);
    });

    const label = host.querySelector<HTMLLabelElement>(".visual-search-dropzone")!;
    const input = host.querySelector<HTMLInputElement>(".visual-search-file-input")!;
    expect(label.htmlFor).toBe(input.id);

    Object.defineProperty(input, "value", {
      configurable: true,
      get: () => "C:\\fakepath\\reference.jpg",
      set: vi.fn(),
    });
    const setter = Object.getOwnPropertyDescriptor(input, "value")!.set as ReturnType<typeof vi.fn>;
    await act(async () => {
      input.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(setter).toHaveBeenCalledWith("");

    await act(async () => root.unmount());
  });

  it("requests a server preview when the browser cannot render the uploaded image", async () => {
    const onPreviewError = vi.fn();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const file = new File(["heic-bytes"], "iphone-photo.heic", { type: "image/heic" });
    const previewUrl = "blob:unsupported-heic";

    await act(async () => {
      root.render(
        <VisualSearchPanel
          {...props()}
          reference={{ kind: "upload", file, previewUrl }}
          onPreviewError={onPreviewError}
        />,
      );
    });

    const image = host.querySelector<HTMLImageElement>(".visual-direct-stage img")!;
    expect(image.src).toContain(previewUrl);
    await act(async () => {
      image.dispatchEvent(new Event("error", { bubbles: true }));
    });

    expect(onPreviewError).toHaveBeenCalledTimes(1);
    expect(onPreviewError).toHaveBeenCalledWith(file, previewUrl);

    await act(async () => root.unmount());
  });

  it("uses separate change-image and close-search actions for an active reference", async () => {
    const onClose = vi.fn();
    const inputClick = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const file = new File(["image"], "reference.jpg", { type: "image/jpeg" });

    await act(async () => {
      root.render(
        <VisualSearchPanel
          {...props()}
          reference={{ kind: "upload", file, previewUrl: "blob:reference" }}
          onClose={onClose}
        />,
      );
    });

    const change = host.querySelector<HTMLButtonElement>('[aria-label="Change image"]')!;
    const close = host.querySelector<HTMLButtonElement>('[aria-label="Close visual search"]')!;
    expect(change).toBeTruthy();
    expect(close).toBeTruthy();

    await act(async () => change.click());
    expect(inputClick).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => close.click());
    expect(onClose).toHaveBeenCalledTimes(1);

    inputClick.mockRestore();
    await act(async () => root.unmount());
  });

  it("rejects a non-image selection with visible feedback instead of silently doing nothing", async () => {
    const onUpload = vi.fn();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<VisualSearchPanel {...props(onUpload)} />);
    });

    const input = host.querySelector<HTMLInputElement>(".visual-search-file-input")!;
    const file = new File(["text"], "notes.txt", { type: "text/plain" });
    Object.defineProperty(input, "files", { configurable: true, value: [file] });

    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(onUpload).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("Choose an image file");

    await act(async () => root.unmount());
  });
});

describe("isVisualSearchImageFile", () => {
  it("accepts image MIME types and safe image-extension fallbacks", () => {
    expect(isVisualSearchImageFile({ name: "a.bin", type: "image/jpeg" })).toBe(true);
    expect(isVisualSearchImageFile({ name: "a.heic", type: "" })).toBe(true);
    expect(isVisualSearchImageFile({ name: "a.webp", type: "application/octet-stream" })).toBe(true);
    expect(isVisualSearchImageFile({ name: "a.exe", type: "" })).toBe(false);
    expect(isVisualSearchImageFile({ name: "a.jpg", type: "text/plain" })).toBe(false);
  });
});
