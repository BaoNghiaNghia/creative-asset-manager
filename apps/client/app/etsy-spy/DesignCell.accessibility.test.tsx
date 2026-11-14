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
    const last = Array.from(dialog?.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-background-swatch",
    ) ?? []).at(-1);
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

  it("lets each table thumbnail use its native width instead of 60x60 attributes", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={listing([
        design("wide-front"),
        {...design("tall-sleeve"), position: "left_sleeve"},
        {...design("square-back"), position: "back"},
      ])} />,
    ));
    const thumbnails = host.querySelectorAll<HTMLButtonElement>(".etsy-design-thumb");
    expect(thumbnails).toHaveLength(3);
    for (const thumb of thumbnails) {
      const img = thumb.querySelector("img");
      expect(img).not.toBeNull();
      expect(img?.hasAttribute("width")).toBe(false);
      expect(img?.hasAttribute("height")).toBe(false);
    }
    expect(host.querySelector(".etsy-design-thumbnails")?.textContent?.trim()).toBe("");
  });

  it("groups placements and keeps multiple variants under their printing position", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    const assets: DesignAsset[] = [
      design("front-a"),
      {...design("front-b"), variant: 2},
      {...design("back-a"), position: "back"},
      {...design("sleeve-a"), position: "left_sleeve"},
    ];
    await act(async () => root?.render(<DesignCell item={listing(assets)} />));
    await act(async () => host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click());

    const gallery = document.body.querySelector<HTMLElement>(".etsy-design-gallery");
    const positions = gallery?.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-position-list button",
    );
    expect(positions).toHaveLength(3);
    expect(positions?.[0].textContent).toContain("Mặt trước");
    expect(positions?.[0].textContent).toContain("2");
    expect(positions?.[1].textContent).toContain("Mặt sau");
    expect(positions?.[2].textContent).toContain("Tay trái");
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button")).toHaveLength(2);
    expect(gallery?.querySelector(".etsy-design-gallery-variant-label")?.textContent)
      .toBe("Mẫu 1");
    await act(async () => gallery?.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-list button",
    )[1]?.click());
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toBe("/original/front-b");
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button[aria-pressed='true']"))
      .toHaveLength(1);

    await act(async () => positions?.[1].click());
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button")).toHaveLength(1);
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toBe("/original/back-a");
    expect(positions?.[1].getAttribute("aria-pressed")).toBe("true");
    await act(async () => gallery?.querySelector<HTMLButtonElement>(
      '[aria-label="Thiết kế trước"]',
    )?.click());
    expect(gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toBe("/original/front-b");
    expect(positions?.[0].getAttribute("aria-pressed")).toBe("true");
  });

  it("disables gallery navigation for a single design but shows its placement", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(<DesignCell item={listing([{
      ...design("only"), position: "right_sleeve",
    }])} />));
    await act(async () => host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click());
    const gallery = document.body.querySelector<HTMLElement>(".etsy-design-gallery");
    expect(gallery?.querySelector(".etsy-design-gallery-position-list button")?.textContent)
      .toContain("Tay phải");
    expect(gallery?.querySelectorAll(".etsy-design-gallery-list button")).toHaveLength(1);
    expect(gallery?.querySelectorAll(".etsy-design-gallery-navigation button[disabled]"))
      .toHaveLength(2);
  });

  it("offers all 22 garment colors, applies the selected background and preserves the artwork", async () => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(<DesignCell item={listing([design("front-a")])} />));
    await act(async () => host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click());

    const gallery = document.body.querySelector<HTMLElement>(".etsy-design-gallery");
    const swatches = Array.from(gallery?.querySelectorAll<HTMLButtonElement>(
      ".etsy-design-gallery-background-swatch",
    ) ?? []);
    expect(swatches).toHaveLength(27); // checkerboard + 22 requested + 4 original extras
    const expected = [
      ["Black", "black", "#25282A"],
      ["White", "white", "#FFFFFF"],
      ["Ivory", "ivory", "#E7CEB5"],
      ["Bay", "bay", "#94A596"],
      ["Sage", "sage", "#3E4827"],
      ["Light Green", "light-green", "#5C7F71"],
      ["Blue Jean", "blue-jean", "#647692"],
      ["Blue Spruce", "blue-spruce", "#3E5D58"],
      ["Butter", "butter", "#F5E1A4"],
      ["Chalky Mint", "chalky-mint", "#5CB8B2"],
      ["Chambray", "chambray", "#BDD6E6"],
      ["Crimson", "crimson", "#A4123F"],
      ["Denim", "denim", "#425563"],
      ["Flo Blue", "flo-blue", "#5576D1"],
      ["Grey", "grey", "#716E6A"],
      ["Orchid", "orchid", "#C7B2DE"],
      ["Pepper", "pepper", "#4E4B48"],
      ["Seafoam", "seafoam", "#487A7B"],
      ["Terracota", "terracota", "#E3775E"],
      ["Watermelon", "watermelon", "#F4364C"],
      ["Lagoon", "lagoon", "#05C3DE"],
      ["Mustard", "mustard", "#F2CD00"],
    ] as const;
    expect(swatches.slice(1, 23).map(option => option.getAttribute("aria-label")))
      .toEqual(expected.map(([label]) => label));
    const stage = gallery?.querySelector<HTMLImageElement>(".etsy-design-gallery-stage img");
    const source = stage?.getAttribute("src");
    for (const [name, id, hex] of expected) {
      const swatch = gallery?.querySelector<HTMLButtonElement>(
        ".etsy-design-gallery-background-" + id,
      );
      expect(swatch?.getAttribute("aria-label")).toBe(name);
      await act(async () => swatch?.click());
      expect(gallery?.dataset.previewBackground).toBe(id);
      expect(gallery?.style.getPropertyValue("--etsy-gallery-bg")).toBe(hex);
      expect(gallery?.querySelectorAll(".etsy-design-gallery-background-swatch[aria-pressed='true']"))
        .toHaveLength(1);
      expect(swatch?.getAttribute("aria-pressed")).toBe("true");
      expect(stage?.getAttribute("src")).toBe(source);
    }
    await act(async () => gallery?.querySelector<HTMLButtonElement>(
      ".etsy-design-gallery-close",
    )?.click());
    await act(async () => host?.querySelector<HTMLButtonElement>(".etsy-design-thumb")?.click());
    expect(document.body.querySelector<HTMLElement>(".etsy-design-gallery")
      ?.dataset.previewBackground).toBe("transparent");
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
    expect(colors).toHaveLength(27);
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
