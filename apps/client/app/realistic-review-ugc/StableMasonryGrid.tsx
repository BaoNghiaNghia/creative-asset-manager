import { Fragment, useEffect, useMemo, useRef, useState, type Key, type ReactNode } from "react";

/**
 * Stable masonry for Stage 0–5 image review dialogs.
 *
 * Native CSS multicolumn balances columns by each image's *current* height.
 * Deferred images change those heights as they load and can repeatedly
 * repack the entire gallery, losing the user's reading/scroll position.
 * Here cards are assigned once by their known (or reserved) aspect ratio.
 * Loading, failure, image decode and status overlays never reorder cards.
 */
export function stableMasonryRatio(width?: number | null, height?: number | null, fallback = 0.8) {
  const candidate = Number(width) > 0 && Number(height) > 0
    ? Number(width) / Number(height)
    : fallback;
  return Number.isFinite(candidate) ? Math.max(0.6, Math.min(1.65, candidate)) : fallback;
}

type StableMasonryGridProps<T> = {
  items: readonly T[];
  getKey: (item: T) => Key;
  getRatio?: (item: T) => number;
  renderItem: (item: T, index: number) => ReactNode;
  className?: string;
  ariaLabel?: string;
  minColumnWidth?: number;
  maxColumns?: number;
};

/**
 * Uses container ResizeObserver only for *actual responsive width* changes,
 * not for image loads. Distribution is deterministic for unchanged data.
 */
export function StableMasonryGrid<T>({
  items, getKey, getRatio, renderItem, className = "", ariaLabel,
  minColumnWidth = 204, maxColumns = 6,
}: StableMasonryGridProps<T>) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [columnCount, setColumnCount] = useState(4);

  useEffect(() => {
    const element = wrapperRef.current;
    if (!element) return;
    const measure = () => {
      const width = Math.max(0, element.clientWidth - 28); // existing gallery padding
      // Narrow touch viewports show two comfortable columns where possible.
      const targetCardWidth = width < 550 ? Math.min(minColumnWidth, 148) : minColumnWidth;
      const columns = Math.max(1, Math.min(maxColumns, Math.floor((width + 12) / (targetCardWidth + 12))));
      setColumnCount(old => old === columns ? old : columns);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [minColumnWidth, maxColumns]);

  const columns = useMemo(() => {
    const result: { item: T; index: number; key: Key }[][] = Array.from(
      { length: columnCount }, () => [],
    );
    const heights = Array(columnCount).fill(0) as number[];
    items.forEach((item, index) => {
      const ratio = Math.max(0.6, Math.min(1.65, getRatio?.(item) || 0.8));
      const colIndex = heights.indexOf(Math.min(...heights));
      result[colIndex].push({ item, index, key: getKey(item) });
      // Height estimate normalized to column width, plus caption/header.
      heights[colIndex] += 1 / ratio + 0.2;
    });
    return result;
  }, [columnCount, getKey, getRatio, items]);

  return <div
    ref={wrapperRef}
    aria-label={ariaLabel}
    className={(className + " rrugc-stable-masonry").trim()}
    style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}
  >
    {columns.map((column, index) => <div className="rrugc-stable-masonry-column" key={index}>
      {column.map(entry => <Fragment key={entry.key}>{renderItem(entry.item, entry.index)}</Fragment>)}
    </div>)}
  </div>;
}
