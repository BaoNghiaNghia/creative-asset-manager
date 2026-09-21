import { useEffect, useRef, useState } from "react";
import type { Asset } from "./api";
import { getFileType } from "../utils/fileType";

const PUBLIC_THUMBNAIL_CONCURRENCY = 6;
const PREFETCH_ROOT_MARGIN = "640px 0px";

type Ticket = { cancel: () => void; release: () => void; setPriority: (priority: number) => void };
type Task = { active: boolean; done: boolean; priority: number; order: number; start: () => void };

export function createPublicThumbnailQueue(limit = PUBLIC_THUMBNAIL_CONCURRENCY) {
  let active = 0;
  let order = 0;
  const pending: Task[] = [];

  const sortPending = () => pending.sort((a, b) => b.priority - a.priority || a.order - b.order);
  const drain = () => {
    sortPending();
    while (active < limit && pending.length) {
      const task = pending.shift();
      if (!task || task.done) continue;
      task.active = true;
      active += 1;
      task.start();
      sortPending();
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
    acquire(start: () => void, priority = 0): Ticket {
      const task: Task = { active: false, done: false, priority, order: order++, start };
      pending.push(task);
      drain();
      return {
        cancel: () => finish(task),
        release: () => finish(task),
        setPriority: nextPriority => {
          if (task.done || task.active || task.priority === nextPriority) return;
          task.priority = nextPriority;
          sortPending();
        },
      };
    },
    activeCount: () => active,
    pendingCount: () => pending.filter(task => !task.done).length,
  };
}

const thumbnailQueue = createPublicThumbnailQueue();
const imageAsset = (asset: Asset) => getFileType(asset.media_type, undefined, asset.filename) === "image";
const videoAsset = (asset: Asset) => getFileType(asset.media_type, undefined, asset.filename) === "video";
export const usesPublicThumbnail = (asset: Asset) => imageAsset(asset) || videoAsset(asset);

export function PublicAssetThumbnail({ asset }: { asset: Asset }) {
  const frameRef = useRef<HTMLSpanElement>(null);
  const ticketRef = useRef<Ticket | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const [inPreloadRange, setInPreloadRange] = useState(false);
  const [inViewport, setInViewport] = useState(false);
  const [grantedUrl, setGrantedUrl] = useState<string>();
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setInPreloadRange(false);
    setInViewport(false);
    setGrantedUrl(undefined);
    setLoaded(false);
    setFailed(false);
  }, [asset.asset_id, asset.source_asset_id, asset.thumbnail_url]);

  useEffect(() => {
    if (!usesPublicThumbnail(asset) || loaded || failed) return;
    const target = frameRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setInPreloadRange(true);
      setInViewport(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      setInPreloadRange(true);
      observer.disconnect();
    }, { rootMargin: PREFETCH_ROOT_MARGIN });
    observer.observe(target);
    return () => observer.disconnect();
  }, [asset.asset_id, asset.source_asset_id, asset.media_type, loaded, failed]);

  useEffect(() => {
    if (!usesPublicThumbnail(asset) || loaded || failed) return;
    const target = frameRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setInViewport(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      setInViewport(entries.some(entry => entry.isIntersecting));
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [asset.asset_id, asset.source_asset_id, asset.media_type, loaded, failed]);

  useEffect(() => {
    ticketRef.current?.setPriority(inViewport ? 1 : 0);
  }, [inViewport]);

  useEffect(() => {
    if (!inPreloadRange || !usesPublicThumbnail(asset) || failed) return;
    const ticket = thumbnailQueue.acquire(() => {
      setGrantedUrl(asset.thumbnail_url);
      timeoutRef.current = window.setTimeout(() => {
        ticket.release();
        if (ticketRef.current === ticket) ticketRef.current = null;
        timeoutRef.current = null;
        setFailed(true);
      }, 30_000);
    }, inViewport ? 1 : 0);
    ticketRef.current = ticket;
    return () => {
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
      ticket.cancel();
      if (ticketRef.current === ticket) ticketRef.current = null;
    };
  }, [inPreloadRange, asset.asset_id, asset.source_asset_id, asset.thumbnail_url, asset.media_type, failed]);

  const finish = () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    ticketRef.current?.release();
    ticketRef.current = null;
  };

  if (!usesPublicThumbnail(asset) || failed) return <span className="public-file-glyph" aria-label={videoAsset(asset) ? "Video" : "File"}>{videoAsset(asset) ? "Video" : "File"}</span>;

  return <span ref={frameRef} className="public-lazy-thumbnail">
    {!loaded && <span className="public-thumbnail-skeleton" aria-hidden="true" />}
    {grantedUrl && <img src={grantedUrl} alt="" loading="eager" decoding="async" onLoad={() => { finish(); setLoaded(true); }} onError={() => { finish(); setFailed(true); }} />}
  </span>;
}

export function PublicGridSkeleton({ count = 12 }: { count?: number }) {
  return <section className="public-grid public-grid-skeleton" role="status" aria-live="polite" aria-label="Loading shared folder">
    {Array.from({ length: count }, (_, index) => <span className="public-card-skeleton" key={index} aria-hidden="true"><i /><i /><i /></span>)}
  </section>;
}
