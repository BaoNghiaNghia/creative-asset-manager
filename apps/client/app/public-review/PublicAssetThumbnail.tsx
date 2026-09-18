import { useEffect, useRef, useState } from "react";
import type { Asset } from "./api";

const PUBLIC_THUMBNAIL_CONCURRENCY = 3;

type Ticket = { cancel: () => void; release: () => void };
type Task = { active: boolean; done: boolean; start: () => void };

export function createPublicThumbnailQueue(limit = PUBLIC_THUMBNAIL_CONCURRENCY) {
  let active = 0;
  const pending: Task[] = [];

  const drain = () => {
    while (active < limit && pending.length) {
      const task = pending.shift();
      if (!task || task.done) continue;
      task.active = true;
      active += 1;
      task.start();
    }
  };
  const finish = (task: Task) => {
    if (task.done) return;
    task.done = true;
    if (task.active) active -= 1;
    else {
      const index = pending.indexOf(task);
      if (index >= 0) pending.splice(index, 1);
    }
    drain();
  };

  return {
    acquire(start: () => void): Ticket {
      const task: Task = { active: false, done: false, start };
      pending.push(task);
      drain();
      return { cancel: () => finish(task), release: () => finish(task) };
    },
    activeCount: () => active,
    pendingCount: () => pending.filter(task => !task.done).length,
  };
}

const thumbnailQueue = createPublicThumbnailQueue();
const imageAsset = (asset: Asset) => (asset.media_type || "").startsWith("image/");
const videoAsset = (asset: Asset) => (asset.media_type || "").startsWith("video/");

export function PublicAssetThumbnail({ asset }: { asset: Asset }) {
  const frameRef = useRef<HTMLSpanElement>(null);
  const ticketRef = useRef<Ticket | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const [inViewport, setInViewport] = useState(false);
  const [grantedUrl, setGrantedUrl] = useState<string>();
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setInViewport(false);
    setGrantedUrl(undefined);
    setLoaded(false);
    setFailed(false);
  }, [asset.asset_id, asset.source_asset_id, asset.thumbnail_url]);

  useEffect(() => {
    if (!imageAsset(asset)) return;
    const target = frameRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setInViewport(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      setInViewport(true);
      observer.disconnect();
    }, { rootMargin: "240px 0px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, [asset.asset_id, asset.source_asset_id, asset.media_type]);

  useEffect(() => {
    if (!inViewport || !imageAsset(asset) || failed) return;
    const ticket = thumbnailQueue.acquire(() => setGrantedUrl(asset.thumbnail_url));
    ticketRef.current = ticket;
    // A stalled connection must not permanently consume a queue slot.
    timeoutRef.current = window.setTimeout(() => {
      ticket.release();
      if (ticketRef.current === ticket) ticketRef.current = null;
      timeoutRef.current = null;
      setFailed(true);
    }, 30_000);
    return () => {
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
      ticket.cancel();
      if (ticketRef.current === ticket) ticketRef.current = null;
    };
  }, [inViewport, asset.asset_id, asset.source_asset_id, asset.thumbnail_url, asset.media_type, failed]);

  const finish = () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    ticketRef.current?.release();
    ticketRef.current = null;
  };

  if (videoAsset(asset)) return <span className="public-video-glyph" aria-label="Video"><i aria-hidden="true">▶</i><span>Video</span></span>;
  if (!imageAsset(asset) || failed) return <span className="public-file-glyph" aria-label="File">File</span>;

  return <span ref={frameRef} className="public-lazy-thumbnail">
    {!loaded && <span className="public-thumbnail-skeleton" aria-hidden="true" />}
    {grantedUrl && <img src={grantedUrl} alt="" loading="lazy" decoding="async" onLoad={() => { finish(); setLoaded(true); }} onError={() => { finish(); setFailed(true); }} />}
  </span>;
}

export function PublicGridSkeleton({ count = 12 }: { count?: number }) {
  return <section className="public-grid public-grid-skeleton" role="status" aria-live="polite" aria-label="Loading shared folder">
    {Array.from({ length: count }, (_, index) => <span className="public-card-skeleton" key={index} aria-hidden="true"><i /><i /><i /></span>)}
  </section>;
}
