import { DeferredImage } from "./DeferredImage";
import { productFabricArtworkUrl } from "./stage2Media";
import { SortDirectionIndicator } from "./SortDirectionIndicator";
import { productProgressLabel, type ProductDesignProgress } from "./ProductsStage2Progress";
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
  progressByPlan?: Readonly<Record<string, ProductDesignProgress>>;
  hatColor?: string;
  selectedPlanId: string | null;
  onSelect: (id: string) => void;
  sortBy: SourcePlanSortBy;
  sortDirection: SourcePlanSortDirection;
  onSortChange: (key: SourcePlanSortBy, direction: SourcePlanSortDirection) => void;
};

export function ProductsStage2SourceTree({
  plans, total, loading, progressByPlan = {}, hatColor = "natural-charcoal",
  selectedPlanId, onSelect, sortBy, sortDirection, onSortChange,
}: Props) {
  const nextSort = sortBy === "source" && sortDirection === "asc" ? "desc" : "asc";
  const completeCount = plans.filter(plan => progressByPlan[plan.id]?.state === "complete").length;
  const activeCount = plans.filter(plan => progressByPlan[plan.id]?.state === "running").length;
  const allVisible = plans.length === total;
  const colorLabel = hatColor.replaceAll("-", " ");
  return <aside className="rrugc-products-source-tree" aria-label="Stage 2 design source tree">
    <header className="rrugc-products-source-tree-head">
      <div className="rrugc-products-source-tree-heading">
        <div className="rrugc-products-source-tree-heading-title">
          <strong>Source tree</strong>
          <span className="rrugc-products-source-tree-count">{total}</span>
        </div>
        <small title={"Ecommerce and scene completion is for hat color " + colorLabel}>
          Stage 1 designs · {colorLabel}
        </small>
      </div>
      <div className="rrugc-products-source-tree-sort" role="columnheader"
        aria-sort={sortBy === "source" ? sortDirection === "asc" ? "ascending" : "descending" : "none"}>
        <button type="button" className="rrugc-colorway-design-sort"
          title="Sort designs by keyword / source name"
          aria-label={`Sort by Embroidery design ${nextSort === "asc" ? "ascending" : "descending"}`}
          onClick={() => onSortChange("source", nextSort)}>
          <span>Keyword</span>
          <SortDirectionIndicator active={sortBy === "source"} direction={sortDirection} />
        </button>
      </div>
    </header>
    <div className="rrugc-products-source-tree-columns">
      <span>ARTWORK / KEYWORD</span>
      <span>22 OUTPUTS</span>
    </div>
    <nav className="rrugc-products-source-tree-list" aria-label="Choose an embroidery design">
      {loading ? Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="rrugc-products-source-tree-skeleton" aria-hidden="true"><span /><span /></div>
      )) : plans.map(plan => {
        const keyword = stage2DesignKeyword(plan);
        const selected = selectedPlanId === plan.id;
        const progress = progressByPlan[plan.id];
        const state = progress?.state || "checking";
        const label = productProgressLabel[state];
        const completed = progress?.completed || 0;
        const totalOutputs = progress?.total || 22;
        const hasSnapshot = state !== "checking" && state !== "unavailable";
        const detail = hasSnapshot
          ? `${completed}/${totalOutputs} outputs · ${label}${progress?.running ? ` · ${progress.running} active` : ""}${progress?.failed ? ` · ${progress.failed} failed` : ""}`
          : label + " output statuses";
        return <button key={plan.id} type="button"
          className={"rrugc-products-source-tree-item" + (selected ? " is-selected" : "")}
          aria-current={selected ? "true" : undefined}
          aria-label={`View design: ${keyword}. ${detail}`}
          title={keyword + " · " + plan.source_name + " · " + detail}
          onClick={() => onSelect(plan.id)}>
          <span className="rrugc-products-source-tree-thumbnail" aria-hidden="true">
            <DeferredImage src={productFabricArtworkUrl(plan.source_preview_url, 128)}
              alt="" rootMargin="160px" loading="lazy" decoding="async" />
          </span>
          <span className="rrugc-products-source-tree-item-info">
            <strong className="rrugc-products-source-tree-keyword">{keyword}</strong>
            <span className="rrugc-products-source-tree-item-meta">
              <span className={"rrugc-products-source-tree-status is-" + state}>
                {state === "complete" ? <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="m4.8 8 2 2 4.4-4.5"/></svg> :
                  <span className="rrugc-products-source-tree-status-dot" aria-hidden="true" />}
                {label}
              </span>
              <span className="rrugc-products-source-tree-output-count">
                {hasSnapshot ? completed + "/" + totalOutputs : "—/" + totalOutputs}
              </span>
            </span>
            <span className={"rrugc-products-source-tree-meter is-" + state} aria-hidden="true">
              <span style={{ width: (hasSnapshot ? completed / totalOutputs * 100 : 0) + "%" }} />
            </span>
          </span>
        </button>;
      })}
      {!loading && !plans.length &&
        <div className="rrugc-products-source-tree-empty">
          No selected Stage 1 designs match this search.
        </div>}
    </nav>
    <footer className="rrugc-products-source-tree-foot">
      <span>{loading ? "Loading designs…" : allVisible ? `All ${total} designs` : `Loaded ${plans.length} / ${total}`}</span>
      {!loading && completeCount > 0 && <span className="rrugc-products-source-tree-summary-done">
        <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="m4.8 8 2 2 4.4-4.5"/></svg>
        {completeCount} complete
      </span>}
      {!loading && activeCount > 0 && <span>{activeCount} running</span>}
    </footer>
  </aside>;
}
