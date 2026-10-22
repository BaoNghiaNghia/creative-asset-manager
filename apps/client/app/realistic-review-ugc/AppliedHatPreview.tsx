import { useEffect, useRef, useState } from "react";
import { useBlueprintEmbroideryComposite } from "./embroidery/useBlueprintEmbroideryComposite";
import type { ThreadMode } from "./embroidery/EmbroideryMaterialCompositor";
import "./AppliedHatPreview.css";

const HAT_VIEWS = [
  { key: "front", label: "front", surface: "front" },
  { key: "side", label: "right side", surface: "side" },
  { key: "back", label: "back", surface: "front" },
] as const;

const STOCK_ROOT = "/rrugc/blueprint/stock/natural-black";

function HatImage({
  designSrc,
  view,
}: {
  designSrc: string;
  view: (typeof HAT_VIEWS)[number];
}) {
  const tileRef = useRef<HTMLDivElement>(null);
  const [nearViewport, setNearViewport] = useState(
    () => typeof IntersectionObserver === "undefined",
  );
  useEffect(() => {
    if (nearViewport) return;
    const tile = tileRef.current;
    if (!tile) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) setNearViewport(true);
    }, { rootMargin: "360px" });
    observer.observe(tile);
    return () => observer.disconnect();
  }, [nearViewport]);

  const hatSrc = view.key === "front"
    ? "/rrugc/blueprint/fronts/natural-black.webp"
    : `${STOCK_ROOT}/${view.key}.jpg`;
  // Each view is composed immediately. No action is required to apply design.
  const embroiderySrc = useBlueprintEmbroideryComposite(
    nearViewport ? designSrc : "",
    hatSrc,
    "black",
    "original" as ThreadMode,
    0,
    view.surface,
    "tile",
  );

  return <div
    className="rrugc-applied-hat-image"
    ref={tileRef}
    role="img"
    aria-label={`Valucap 8869 ${view.label} view with embroidery design applied`}
  >
    <img src={hatSrc} alt="" aria-hidden="true" loading="lazy" />
    {nearViewport && <img
      className={`rrugc-applied-hat-artwork is-${view.key}`}
      src={embroiderySrc}
      alt=""
      aria-hidden="true"
      loading="lazy"
    />}
  </div>;
}

export function AppliedHatPreview({
  designSrc,
  designName,
  revision,
}: {
  designSrc: string;
  designName: string;
  revision: string;
}) {
  return <div
    className="rrugc-applied-hat-grid"
    key={revision}
    aria-label="Embroidery design and three applied hat views"
  >
    <div className="rrugc-applied-hat-image" role="img" aria-label={`Original embroidery design: ${designName}`}>
      <img src={designSrc} alt="" aria-hidden="true" loading="lazy" />
    </div>
    {HAT_VIEWS.map(view => <HatImage
      key={view.key}
      designSrc={designSrc}
      view={view}
    />)}
  </div>;
}
