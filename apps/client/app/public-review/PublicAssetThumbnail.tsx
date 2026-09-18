import { useEffect, useRef, useState } from "react";
import type { Asset } from "./api";

const imageAsset = (asset: Asset) => (asset.media_type || "").startsWith("image/");
const videoAsset = (asset: Asset) => (asset.media_type || "").startsWith("video/");

export function PublicAssetThumbnail({ asset }: { asset: Asset }) {
  const frameRef = useRef<HTMLSpanElement>(null);
  const [inViewport, setInViewport] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setInViewport(false);
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

  if (videoAsset(asset)) return <span className="public-video-glyph" aria-label="Video"><i aria-hidden="true">▶</i><span>Video</span></span>;
  if (!imageAsset(asset) || failed) return <span className="public-file-glyph" aria-label="File">File</span>;

  return <span ref={frameRef} className="public-lazy-thumbnail">
    {!loaded && <span className="public-thumbnail-skeleton" aria-hidden="true" />}
    {inViewport && <img src={asset.thumbnail_url} alt="" loading="lazy" decoding="async" onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />}
  </span>;
}

export function PublicGridSkeleton({ count = 12 }: { count?: number }) {
  return <section className="public-grid public-grid-skeleton" role="status" aria-live="polite" aria-label="Loading shared folder">
    {Array.from({ length: count }, (_, index) => <span className="public-card-skeleton" key={index} aria-hidden="true"><i /><i /><i /></span>)}
  </section>;
}
