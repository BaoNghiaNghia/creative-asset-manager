import { useEffect, useRef, useState } from "react";
import { useBlueprintEmbroideryComposite } from "./embroidery/useBlueprintEmbroideryComposite";
import { useBlueprintArtworkFit } from "./BlueprintDesignFit";
import { DEFAULT_FRONT_PLACEMENT, frontPlacementStyle, type FrontPlacement } from "./FrontPlacement";
import "./AppliedHatPreview.css";
import { DeferredImage } from "./DeferredImage";
import { productEmbroideryArtworkUrl, productFabricArtworkUrl, publicHatStockUrl } from "./stage2Media";

const HAT_VIEWS = [
  { key: "front", label: "front" },
  { key: "side", label: "right side" },
  { key: "back", label: "back" },
] as const;

const STOCK_ROOT = "/rrugc/blueprint/stock/natural-black";

function HatImage({ designSrc, view, stockViews, placement }: {
  designSrc: string;
  view: (typeof HAT_VIEWS)[number];
  stockViews?: Partial<Record<"front" | "side" | "back", string>>;
  placement: FrontPlacement;
}) {
  const tileRef = useRef<HTMLDivElement>(null);
  const [nearViewport, setNearViewport] = useState(() => typeof IntersectionObserver === "undefined");
  const front = view.key === "front";
  // Reuse the exact same painted-artwork fit at 100% as the Blueprint
  // 'Front embroidery area', including transparent PNG padding removal.
  const fit = useBlueprintArtworkFit(front ? designSrc : "", 100);
  useEffect(() => {
    if (nearViewport || !front) return;
    const tile = tileRef.current;
    if (!tile) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) setNearViewport(true);
    }, { rootMargin: "360px" });
    observer.observe(tile);
    return () => observer.disconnect();
  }, [nearViewport, front]);

  // Keep all three cap angles on the same official Natural/Black stock set.
  const hatSrc = publicHatStockUrl(stockViews?.[view.key] || `${STOCK_ROOT}/${view.key}.jpg`);
  const embroiderySrc = useBlueprintEmbroideryComposite(
    front && nearViewport ? designSrc : "",
    hatSrc,
    "black",
    "original",
    0,
    "front",
    "tile",
  );

  return <div className="rrugc-applied-hat-image" ref={tileRef} role="img"
    aria-label={`Valucap 8869 ${view.label} view${front ? " with front embroidery design applied" : " without applied embroidery"}`}>
    {front ? <div className="rrugc-applied-hat-front-scene" style={frontPlacementStyle(placement)}>
      <DeferredImage className="rrugc-applied-hat-stock" src={hatSrc} alt="" aria-hidden="true" rootMargin="350px" />
      {nearViewport && <div className="rrugc-applied-hat-front-frame" ref={fit.frameRef}>
        <img className="rrugc-applied-hat-front-artwork"
          src={embroiderySrc} onLoad={fit.onLoad}
          style={fit.style ?? { opacity: 0 }} alt="" aria-hidden="true" />
      </div>}
    </div> : <DeferredImage src={hatSrc} alt="" aria-hidden="true" rootMargin="350px" />}
  </div>;
}

export function AppliedHatPreview({ designSrc, designName, revision, stockViews, placement = DEFAULT_FRONT_PLACEMENT }: {
  designSrc: string;
  designName: string;
  revision: string;
  stockViews?: Partial<Record<"front" | "side" | "back", string>>;
  placement?: FrontPlacement;
}) {
  // Reuse one alpha-preserving, cached source for both the original tile and
  // the stitched front: no black Drive matte, no duplicate original download.
  const artworkSrc = productEmbroideryArtworkUrl(designSrc);
  const fabricPhotoSrc = productFabricArtworkUrl(designSrc);
  return <div className="rrugc-applied-hat-grid" key={revision}
    aria-label="Original embroidery design, embroidered front hat, and unembroidered side and back references">
    <div className="rrugc-applied-hat-image is-original-design" role="img" aria-label={`Original embroidery design: ${designName}`}>
      <DeferredImage src={fabricPhotoSrc} alt="" aria-hidden="true" rootMargin="350px" />
    </div>
    {HAT_VIEWS.map(view => <HatImage key={view.key} designSrc={artworkSrc} view={view} stockViews={stockViews} placement={placement} />)}
  </div>;
}
