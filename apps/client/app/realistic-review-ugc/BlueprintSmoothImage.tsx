import { useEffect, useRef, useState, type ImgHTMLAttributes } from "react";

/** Bounded neighbor prefetch: URLs are deduplicated and benefit from browser HTTP cache. */
const requestedImages = new Set<string>();
const inflightImages = new Map<string, HTMLImageElement>();
export function prefetchBlueprintImage(src: string) {
  if (!src || requestedImages.has(src) || typeof Image === "undefined") return;
  requestedImages.add(src);
  const image = new Image();
  image.decoding = "async";
  inflightImages.set(src, image);
  image.onload = () => { inflightImages.delete(src); };
  image.onerror = () => { inflightImages.delete(src); requestedImages.delete(src); };
  image.src = src;
}

type SmoothProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & { src: string };

/** High-priority active preview, independent from the thumbnail loading queue. */
export function BlueprintSmoothImage({ src, className = "", onLoad, onError, ...props }: SmoothProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [loadedSource, setLoadedSource] = useState<string | null>(null);
  const [failedSource, setFailedSource] = useState<string | null>(null);

  useEffect(() => {
    const img = imageRef.current;
    if (img?.complete && img.naturalWidth > 0) setLoadedSource(src);
  }, [src]);

  const loaded = loadedSource === src;
  const failed = failedSource === src;
  return <img
    {...props}
    ref={imageRef}
    src={src}
    loading="eager"
    decoding="async"
    className={`${className} rrugc-blueprint-smooth-img${loaded ? " is-loaded" : ""}${failed ? " is-failed" : ""}`.trim()}
    onLoad={event => { setLoadedSource(src); setFailedSource(null); onLoad?.(event); }}
    onError={event => { setFailedSource(src); onError?.(event); }}
  />;
}
