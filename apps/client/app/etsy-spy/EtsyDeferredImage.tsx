import { memo, useCallback, useEffect, useRef, useState, type ImgHTMLAttributes } from "react";

// One observer for every Etsy Spy thumbnail. Native loading=lazy alone leaves
// too many eager image requests in Chrome's wide near-viewport heuristic,
// particularly when a horizontally scrollable table is on a narrow phone.
const observed = new Map<Element, () => void>();
let sharedObserver: IntersectionObserver | null = null;

function observeNearViewport(node: Element, reveal: () => void): () => void {
  if (typeof IntersectionObserver === "undefined") {
    reveal();
    return () => {};
  }
  if (!sharedObserver) {
    sharedObserver = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const notify = observed.get(entry.target);
        if (!notify) continue;
        observed.delete(entry.target);
        sharedObserver?.unobserve(entry.target);
        notify();
      }
      if (observed.size === 0) {
        sharedObserver?.disconnect();
        sharedObserver = null;
      }
    }, { root: null, rootMargin: "240px 64px", threshold: 0.01 });
  }
  observed.set(node, reveal);
  sharedObserver.observe(node);
  return () => {
    if (observed.get(node) === reveal) {
      observed.delete(node);
      sharedObserver?.unobserve(node);
      if (observed.size === 0) {
        sharedObserver?.disconnect();
        sharedObserver = null;
      }
    }
  };
}

// Prevent a rapid scroll through Picked from flooding the API and Drive with
// requests. Only thumbnails participate; the explicitly opened gallery image
// bypasses the gate. Pending requests are cancelled when rows unmount/change.
const THUMBNAIL_CONCURRENCY = 4;
const THUMBNAIL_TIMEOUT_MS = 20_000;
let activeThumbnails = 0;
const thumbnailWaiters: Array<{ run: () => void; cancelled: boolean }> = [];

function drainThumbnailQueue(): void {
  while (activeThumbnails < THUMBNAIL_CONCURRENCY && thumbnailWaiters.length) {
    const waiter = thumbnailWaiters.shift()!;
    if (!waiter.cancelled) waiter.run();
  }
}

function queueThumbnail(
  begin: (finish: () => void) => void, expire: () => void,
): () => void {
  let finishCurrent: (() => void) | null = null;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const entry = {
    cancelled: false,
    run: () => {
      activeThumbnails += 1;
      let released = false;
      const finish = () => {
        if (released) return;
        released = true;
        if (timeout !== undefined) clearTimeout(timeout);
        activeThumbnails = Math.max(0, activeThumbnails - 1);
        drainThumbnailQueue();
      };
      finishCurrent = finish;
      // Cancel a stalled image request, freeing the slot instead of allowing
      // a few broken requests to prevent every remaining thumbnail loading.
      timeout = setTimeout(() => {
        if (!entry.cancelled) expire();
        finish();
      }, THUMBNAIL_TIMEOUT_MS);
      begin(finish);
    },
  };
  thumbnailWaiters.push(entry);
  drainThumbnailQueue();
  return () => {
    entry.cancelled = true;
    finishCurrent?.();
  };
}

type DeferredImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>,
  "src" | "loading" | "decoding" | "onLoad" | "onError"> & {
  src: string;
  eager?: boolean;
  onFailure?: () => void;
};

/** A fixed-size image skeleton that does not start network work until nearby. */
export const EtsyDeferredImage = memo(function EtsyDeferredImage({
  src, className, onFailure, eager = false, ...imageProps
}: DeferredImageProps) {
  const target = useRef<HTMLImageElement | null>(null);
  const [nearUrl, setNearUrl] = useState<string | null>(() =>
    eager || typeof IntersectionObserver === "undefined" ? src : null);
  const [requestedUrl, setRequestedUrl] = useState<string | null>(null);
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const finishRef = useRef<(() => void) | null>(null);
  const reveal = useCallback(() => setNearUrl(src), [src]);
  const requested = eager || requestedUrl === src;
  const loaded = loadedUrl === src;
  const failed = failedUrl === src;

  useEffect(() => {
    if (requested || !target.current) return;
    return observeNearViewport(target.current, reveal);
  }, [requested, reveal]);

  useEffect(() => {
    if (eager || nearUrl !== src) return;
    return queueThumbnail(finish => {
      finishRef.current = finish;
      setRequestedUrl(src);
    }, () => setFailedUrl(src));
  }, [src, eager, nearUrl]);

  useEffect(() => {
    if (!requested || loaded || failed) return;
    const image = target.current;
    // Browser cache can resolve synchronously before React's effect runs.
    if (image?.complete && image.naturalWidth > 0) {
      finishRef.current?.();
      finishRef.current = null;
      setLoadedUrl(src);
    }
  }, [src, requested, loaded, failed]);

  const state = failed ? "error" : loaded ? "ready" : requested ? "loading" : "deferred";
  return <>
    {(state === "loading" || state === "deferred") &&
      <span className="etsy-image-skeleton" data-busy={state === "loading"} aria-hidden="true" />}
    {failed && <span className="etsy-image-error" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="3" y="4" width="18" height="16" rx="3" />
        <path d="m4 17 5-5 3 3 3-4 5 6M3 3l18 18" />
      </svg>
    </span>}
    <img {...imageProps} ref={target}
      className={className}
      src={requested && !failed ? src : undefined}
      loading={eager ? "eager" : "lazy"} decoding="async"
      data-image-state={state}
      onPointerEnter={reveal}
      onFocus={reveal}
      onLoad={() => {
        finishRef.current?.();
        finishRef.current = null;
        setLoadedUrl(src);
      }}
      onError={() => {
        finishRef.current?.();
        finishRef.current = null;
        setFailedUrl(src);
        onFailure?.();
      }}
    />
  </>;
});
