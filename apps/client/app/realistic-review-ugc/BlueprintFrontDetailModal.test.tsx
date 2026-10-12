// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BlueprintFrontDetailModal } from "./BlueprintFrontDetailModal";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const composite = vi.hoisted(() => vi.fn(() => "blob:front-detail-hq"));
vi.mock("./embroidery/useBlueprintEmbroideryComposite", () => ({
  useBlueprintEmbroideryComposite: composite,
}));
vi.mock("./BlueprintDesignFit", () => ({
  useBlueprintArtworkFit: () => ({
    frameRef: { current: null },
    onLoad: () => undefined,
    style: { position: "absolute", width: 560, height: 560, left: 0, top: 0 },
  }),
}));

afterEach(() => {
  document.body.replaceChildren();
  composite.mockClear();
});

describe("Front embroidery detail high-resolution rendering", () => {
  it("renders the native-source HQ material only inside detail, preserving thread mode and preview fallback", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const base = {
      hatSrc: "/rrugc/blueprint/fronts/black.jpg",
      designSrc: "/api/generation/original.png",
      compositedSrc: "blob:front-preview",
      colorId: "black",
      colorLabel: "Natural / Black",
      designLabel: "DIBS ON THE GINGER",
      designSize: 100,
      guides: true,
      onClose: () => undefined,
    };
    await act(async () => root.render(<BlueprintFrontDetailModal {...base} threadMode="auto" />));
    expect(composite).toHaveBeenCalledWith(
      base.designSrc, base.hatSrc, "black", "auto", 0, "front", "detail", base.compositedSrc,
    );
    expect(document.querySelector<HTMLImageElement>(
      ".rrugc-blueprint-detail-image .rrugc-blueprint-selected-design img",
    )?.getAttribute("src")).toBe("blob:front-detail-hq");
    expect(document.querySelector(".rrugc-blueprint-front-guide")).not.toBeNull();
    await act(async () => root.render(<BlueprintFrontDetailModal {...base} threadMode="original" />));
    expect(composite).toHaveBeenLastCalledWith(
      base.designSrc, base.hatSrc, "black", "original", 0, "front", "detail", base.compositedSrc,
    );
    await act(async () => root.unmount());
  });
});
