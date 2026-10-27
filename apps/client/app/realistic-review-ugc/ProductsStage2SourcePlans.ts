import type { SourcePlanPage } from "./types";
import type { SourcePlanSortBy, SourcePlanSortDirection } from "./api";

export const PRODUCT_STAGE2_SOURCE_BATCH_SIZE = 500;

type SourceFilters = {
  page: number;
  pageSize: number;
  query: string;
  sortBy: SourcePlanSortBy;
  sortDirection: SourcePlanSortDirection;
  colorwaySources: true;
  colorwayWorkflow: "products";
};
type SourcePageLoader = (filters: SourceFilters, signal?: AbortSignal) => Promise<SourcePlanPage>;

/** Load every selected Stage 1 design, not just the first 20 displayed by the
 * old paginated table. The server caps each call at 500 so continue for larger
 * libraries; fetch sequentially to avoid a source/DB request burst. */
export async function loadAllProductStage2SourcePlans(
  loadPage: SourcePageLoader,
  query: string,
  sortBy: SourcePlanSortBy,
  sortDirection: SourcePlanSortDirection,
  signal?: AbortSignal,
): Promise<SourcePlanPage> {
  const filters: SourceFilters = {
    page: 1, pageSize: PRODUCT_STAGE2_SOURCE_BATCH_SIZE,
    query, sortBy, sortDirection, colorwaySources: true, colorwayWorkflow: "products",
  };
  const first = await loadPage(filters, signal);
  const unique = new Map(first.items.map(plan => [plan.id, plan]));
  const lastPage = Math.ceil(first.total / PRODUCT_STAGE2_SOURCE_BATCH_SIZE);
  for (let page = 2; page <= lastPage; page++) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    const next = await loadPage({ ...filters, page }, signal);
    next.items.forEach(plan => unique.set(plan.id, plan));
  }
  if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
  const items = [...unique.values()];
  return { ...first, items, page: 1, page_size: Math.max(items.length, 1) };
}
