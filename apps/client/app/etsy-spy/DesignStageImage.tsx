import { useEffect, useState } from "react";

type LoadState = "loading" | "ready" | "error";

type DesignStageImageProps = {
  originalUrl: string;
  thumbnailUrl: string;
  alt: string;
};

const SLOW_IMAGE_MS = 12_000;

/**
 * Non-blocking Design gallery image: display an already available thumbnail
 * while the larger private Drive PNG loads. A slow/broken original never
 * leaves a full-size skeleton covering the artwork or chosen garment color.
 *
 * Mount this keyed by immutable design identity. Background swatch changes
 * do not remount the images or initiate any extra image requests.
 */
export function DesignStageImage({
  originalUrl, thumbnailUrl, alt,
}: DesignStageImageProps) {
  const [originalState, setOriginalState] = useState<LoadState>("loading");
  const [thumbnailState, setThumbnailState] = useState<LoadState>("loading");
  const [slow, setSlow] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (originalState !== "loading") return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_IMAGE_MS);
    return () => window.clearTimeout(timer);
  }, [originalState, retry, originalUrl]);

  const retryOriginal = () => {
    setSlow(false);
    setOriginalState("loading");
    setRetry(count => count + 1);
  };
  // Bypass a cached failure only when the operator explicitly retries.
  const requestedOriginal = retry
    ? originalUrl + (originalUrl.includes("?") ? "&" : "?") + "retry=" + retry
    : originalUrl;
  const showingThumbnail = originalState !== "ready" && thumbnailState === "ready";

  return <div className="etsy-design-gallery-artwork"
    data-preview-quality={originalState === "ready" ? "original"
      : showingThumbnail ? "thumbnail" : originalState === "error" ? "error" : "loading"}>
    <img key={requestedOriginal}
      className="etsy-design-gallery-artwork-original"
      data-render-state={originalState}
      src={requestedOriginal}
      alt={alt}
      decoding="async"
      loading="eager"
      onLoad={() => { setOriginalState("ready"); setSlow(false); }}
      onError={() => setOriginalState("error")} />
    <img className="etsy-design-gallery-artwork-thumb"
      data-render-state={thumbnailState}
      src={thumbnailUrl}
      alt=""
      aria-hidden="true"
      decoding="async"
      loading="eager"
      onLoad={() => setThumbnailState("ready")}
      onError={() => setThumbnailState("error")} />
    {originalState === "loading" && thumbnailState !== "ready" &&
      <div className="etsy-design-gallery-image-loading" role="status">
        <span className="etsy-design-gallery-image-spinner" aria-hidden="true" />
        <span>
          <strong>Đang mở thiết kế</strong>
          <small>{slow ? "Ảnh gốc đang tải chậm hơn bình thường" : "Hiển thị ảnh xem trước..."}</small>
        </span>
      </div>}
    {originalState === "loading" && thumbnailState === "ready" &&
      <div className="etsy-design-gallery-image-footnote" role="status">
        <span className="etsy-design-gallery-image-spinner" aria-hidden="true" />
        {slow ? "Đang tải ảnh gốc — vẫn xem được bản thu nhỏ" : "Đang tải ảnh chất lượng cao"}
      </div>}
    {originalState === "error" &&
      <div className={"etsy-design-gallery-image-fallback" + (thumbnailState !== "ready" ? " is-empty" : "")}
        role="status">
        <span>{thumbnailState === "ready"
          ? "Đang hiển thị bản xem trước"
          : thumbnailState === "error" ? "Chưa thể tải ảnh thiết kế" : "Đang tải ảnh xem trước"}</span>
        <button type="button" className="etsy-design-gallery-image-retry"
          onClick={retryOriginal} aria-label="Thử tải lại ảnh gốc">
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="none"
            stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 11a8 8 0 1 1-2.5-5.7M20 4v6h-6"/>
          </svg>
          Thử lại
        </button>
      </div>}
  </div>;
}
