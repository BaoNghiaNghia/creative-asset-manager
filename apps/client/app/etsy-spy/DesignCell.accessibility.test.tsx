// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DesignCell } from "./DesignCell";
import type { Listing, DesignAsset } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const design = (id: string): DesignAsset => ({
  id, job_id: "job-1", listing_key: "123", position: "front",
  variant: 1, method: "chatgpt", status: "needs_review", source: "mapped",
  output_sha256: "a".repeat(64), thumbnail_url: "/thumb/" + id,
  original_url: "/original/" + id, print_ready: false,
  rights_status: "unverified", manual_qa_required: true,
});

describe("Picked Design gallery accessibility and refresh", () => {
  let host: HTMLDivElement | null = null;
  let root: Root | null = null;
  const listing = (designs: DesignAsset[]) => ({
    listingKey: "123", pageTitle: "Sample Shirt", designs,
  }) as Listing;

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    host?.remove();
    root = null; host = null;
    document.body.style.overflow = "";
  });

  it("locks background scroll, traps Tab, survives design refresh and restores focus on Escape", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={listing([design("a"), design("b")])} />,
    ));
    const opener = host.querySelector<HTMLButtonElement>(".etsy-design-thumb");
    expect(opener).not.toBeNull();
    document.body.style.overflow = "auto";
    await act(async () => opener?.click());
    const dialog = document.body.querySelector<HTMLElement>(".etsy-design-gallery");
    const close = dialog?.querySelector<HTMLButtonElement>(".etsy-design-gallery-close");
    const last = dialog?.querySelectorAll<HTMLButtonElement>(".etsy-design-gallery-background-swatch")[7];
    expect(dialog).not.toBeNull();
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.activeElement).toBe(close);

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown",
        {key: "Tab", shiftKey: true, bubbles: true, cancelable: true}));
    });
    expect(document.activeElement).toBe(last);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown",
        {key: "Tab", bubbles: true, cancelable: true}));
    });
    expect(document.activeElement).toBe(close);

    await act(async () => root?.render(
      <DesignCell item={listing([design("a"), design("b"), design("c")])} />,
    ));
    expect(document.body.querySelector(".etsy-design-gallery")).toBe(dialog);
    expect(document.body.style.overflow).toBe("hidden");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown",
        {key: "Escape", bubbles: true, cancelable: true}));
    });
    expect(document.body.querySelector(".etsy-design-gallery")).toBeNull();
    expect(document.body.style.overflow).toBe("auto");
    expect(document.activeElement).toBe(opener);
  });

  it("starts with checkerboard, switches the preview without changing the PNG, and resets on reopening", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={listing([design("a"), design("b")])} />,
    ));
    const opener = host.querySelector<HTMLButtonElement>(".etsy-design-thumb");
    await act(async () => opener?.click());
    const gallery = document.body.querySelector<HTMLElement>(".etsy-design-gallery");
    const stage = gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img");
    const initialSrc = stage?.getAttribute("src");
    expect(gallery?.dataset.previewBackground).toBe("transparent");
    const colors = Array.from(gallery?.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-background-swatch",
    ) ?? []);
    expect(colors).toHaveLength(8);
    expect(colors[0]?.getAttribute("aria-pressed")).toBe("true");

    const navy = gallery?.querySelector<HTMLButtonElement>(
      '.etsy-design-gallery-background-navy',
    );
    await act(async () => navy?.click());
    expect(gallery?.dataset.previewBackground).toBe("navy");
    expect(navy?.getAttribute("aria-pressed")).toBe("true");
    expect(colors.filter(button => button.getAttribute("aria-pressed") === "true"))
      .toHaveLength(1);
    expect(stage?.getAttribute("src")).toBe(initialSrc);
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button")).toHaveLength(2);

    await act(async () => gallery?.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-list button",
    )[1]?.click());
    expect(gallery?.dataset.previewBackground).toBe("navy");
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toBe("/original/b");

    await act(async () => root?.render(<DesignCell item={listing([
      design("a"), design("b"), design("c"),
    ])} />));
    expect(document.body.querySelector(".etsy-design-gallery")).toBe(gallery);
    expect(gallery?.dataset.previewBackground).toBe("navy");

    await act(async () => {
      gallery?.querySelector<HTMLButtonElement>(".etsy-design-gallery-close")?.click();
    });
    await act(async () => opener?.click());
    expect(document.body.querySelector<HTMLElement>(".etsy-design-gallery")
      ?.dataset.previewBackground).toBe("transparent");
    expect(document.body.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-background-transparent",
    )?.getAttribute("aria-pressed")).toBe("true");
  });
});
