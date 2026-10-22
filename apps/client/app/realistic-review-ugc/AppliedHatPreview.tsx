import { useState } from "react";
import { useBlueprintEmbroideryComposite } from "./embroidery/useBlueprintEmbroideryComposite";
import type { ThreadMode } from "./embroidery/EmbroideryMaterialCompositor";
import "./AppliedHatPreview.css";

/** Visual preview only. These blob previews must NOT be passed to a server skill as durable assets. */
const VIEWS = [
  { key: "front", title: "Front", surface: "front", description: "Front center embroidery" },
  { key: "side", title: "Angle", surface: "side", description: "Right-side embroidery placement" },
  { key: "back", title: "Back", surface: "front", description: "Reference only; do not invent rear embroidery" }
] as const;

const ROOT = "/rrugc/blueprint/stock/natural-black";
type View = (typeof VIEWS)[number]["key"];

function HatCard({ designSrc, view, enabled, onToggle }: {
  designSrc: string;
  view: (typeof VIEWS)[number];
  enabled: boolean;
  onToggle: () => void;
}) {
  const hatSrc = ROOT + "/" + view.key + ".jpg";
  const materialSrc = useBlueprintEmbroideryComposite(
    enabled && view.key !== "back" ? designSrc : "", hatSrc, "black", "original" as ThreadMode,
    0, view.surface, "preview",
  );
  return <section className="rrugc-applied-hat-card" aria-label={view.title + " hat preview"}>
    <div className="rrugc-applied-hat-heading">
      <strong>{view.title}</strong>
      <span>{enabled ? "Preview" : "Not applied"}</span>
    </div>
    <div className="rrugc-applied-hat-image">
      <img src={hatSrc} alt={"Natural black 8869 hat " + view.title.toLowerCase()} loading="lazy" />
      {enabled && view.key !== "back" && <img className={"rrugc-applied-hat-artwork is-" + view.key}
        src={materialSrc} alt="" aria-hidden="true" />}
    </div>
    <small>{view.description}</small>
    <button type="button" onClick={onToggle} disabled={view.key === "back"}
      title={view.key === "back" ? "Rear view remains a faithful unembroidered stock reference" : undefined}>
      {view.key === "back" ? "Reference view" : enabled ? "Reset preview" : "Apply preview"}
    </button>
  </section>;
}

export function AppliedHatPreview({ designSrc, revision }: { designSrc: string; revision: string }) {
  const [state, setState] = useState<{ revision: string; enabled: Record<View, boolean> }>({
    revision, enabled: { front: false, side: false, back: false },
  });
  const active = state.revision === revision
    ? state.enabled : { front: false, side: false, back: false };
  return <div className="rrugc-applied-hat-section" aria-label="Applied hat previews">
    <div className="rrugc-applied-hat-top">
      <strong>Hat previews</strong>
      <span>Visual proof only · not saved skill inputs</span>
    </div>
    <button type="button" className="rrugc-applied-hat-all"
      onClick={() => setState({ revision, enabled: { front: true, side: true, back: false } })}>
      Apply design previews
    </button>
    <div className="rrugc-applied-hat-grid">
      {VIEWS.map(view => <HatCard key={view.key} designSrc={designSrc} view={view}
        enabled={active[view.key]}
        onToggle={() => setState({ revision, enabled: { ...active, [view.key]: !active[view.key] } })} />)}
    </div>
  </div>;
}
