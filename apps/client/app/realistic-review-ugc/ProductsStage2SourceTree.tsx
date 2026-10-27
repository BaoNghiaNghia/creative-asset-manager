import { DeferredImage } from "./DeferredImage";
import { productFabricArtworkUrl } from "./stage2Media";
import { SortDirectionIndicator } from "./SortDirectionIndicator";
import type { SourcePlanSortBy, SourcePlanSortDirection } from "./api";
import type { SourcePlan } from "./types";
import "./ProductsStage2SourceTree.css";

/** Stage 1's saved image-output version is the source of truth, not design_04.png. */
export function stage2DesignKeyword(plan: SourcePlan): string {
  const original = plan.source_keyword?.trim();
  if (original) return original;
  const visualText = plan.visual_context?.embroidery_text?.map(value => value.trim()).filter(Boolean);
  if (visualText?.length) return visualText.join(" / ");
  return plan.source_name.replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ").trim() || "Untitled design";
}

type Props = {
  plans: SourcePlan[];
  total: number;
  loading: boolean;
  selectedPlanId: string | null;
  onSelect: (id: string) => void;
  sortBy: SourcePlanSortBy;
  sortDirection: SourcePlanSortDirection;
  onSortChange: (key: SourcePlanSortBy, direction: SourcePlanSortDirection) => void;
};

export function ProductsStage2SourceTree({
  plans, total, loading, selectedPlanId, onSelect, sortBy, sortDirection, onSortChange,
}: Props) {
  const nextSort = sortBy === "source" && sortDirection === "asc" ? "desc" : "asc";
  return <aside className="rrugc-products-source-tree" aria-label="Stage 2 design source tree">
    <header className="rrugc-products-source-tree-head">
      <div>
        <strong>Source tree</strong>
        <span>{total} selected Stage 1 designs</span>
      </div>
      <span className="rrugc-products-source-tree-min" title="Drag the divider to resize; minimum 20% of the card">20% min</span>
    </header>
    <div className="rrugc-products-source-tree-sort" role="columnheader"
      aria-sort={sortBy === "source" ? sortDirection === "asc" ? "ascending" : "descending" : "none"}>
      <button type="button" className="rrugc-colorway-design-sort"
        aria-label={`Sort by Embroidery design ${nextSort === "asc" ? "ascending" : "descending"}`}
        onClick={() => onSortChange("source", nextSort)}>
        <span>Thumbnail / Keyword</span>
        <SortDirectionIndicator active={sortBy === "source"} direction={sortDirection} />
      </button>
    </div>
    <nav className="rrugc-products-source-tree-list" aria-label="Choose an embroidery design">
      {loading ? Array.from({ length: 5 }, (_, index) => (
        <div key={index} className="rrugc-products-source-tree-skeleton" aria-hidden="true"><span /><span /></div>
      )) : plans.map(plan => {
        const keyword = stage2DesignKeyword(plan);
        const selected = selectedPlanId === plan.id;
        return <button key={plan.id} type="button"
          className={"rrugc-products-source-tree-item" + (selected ? " is-selected" : "")}
          aria-current={selected ? "true" : undefined}
          aria-label={`View design: ${keyword} (${plan.source_name})`}
          title={keyword + " · " + plan.source_name}
          onClick={() => onSelect(plan.id)}>
          <span className="rrugc-products-source-tree-thumbnail" aria-hidden="true">
            <DeferredImage src={productFabricArtworkUrl(plan.source_preview_url, 128)}
              alt="" rootMargin="160px" loading="lazy" decoding="async" />
          </span>
          <span className="rrugc-products-source-tree-item-info">
            <strong>{keyword}</strong>
            <small>{plan.source_name}</small>
          </span>
        </button>;
      })}
      {!loading && !plans.length &&
        <div className="rrugc-products-source-tree-empty">No Stage 1 designs found on this page.</div>}
    </nav>
    <footer className="rrugc-products-source-tree-foot">
      {loading ? "Loading designs…" : `Showing ${plans.length} of ${total} designs`}
    </footer>
  </aside>;
}
