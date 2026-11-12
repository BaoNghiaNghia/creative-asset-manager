import type { Listing, Page } from "./api";

/**
 * Keep unchanged Picked row objects stable across background Scout/SMS polls.
 * The view only consumes page counts, source status and the row projections;
 * last_synced_at alone must not churn every row or an open dialog.
 */
export function reconcilePickedPage(
  previous: Page<Listing>,
  incoming: Page<Listing>,
): Page<Listing> {
  const byKey = new Map(previous.items.map(item => [item.listingKey, item]));
  let changed = previous.items.length !== incoming.items.length;
  const items = incoming.items.map((item, index) => {
    const old = byKey.get(item.listingKey);
    if (old && JSON.stringify(old) === JSON.stringify(item)) {
      if (previous.items[index] !== old) changed = true;
      return old;
    }
    changed = true;
    return item;
  });

  if (!changed &&
    previous.page === incoming.page &&
    previous.page_size === incoming.page_size &&
    previous.total === incoming.total &&
    previous.pages === incoming.pages &&
    previous.source_status === incoming.source_status) {
    return previous;
  }
  return { ...incoming, items };
}
