import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { spyFetch, type Listing, type DesignAsset } from "./api";
import { sharedGarmentColors } from "./productColorMatch";
import { EtsyDeferredImage } from "./EtsyDeferredImage";
import { DesignZoomStage } from "./DesignZoomStage";
import { C1717_SMS_COLORS } from "./c1717SmsColors";

// SMS snapshot: 72 C1717 colorways. Only configured colors with a known HEX
// participate in automatic recommendations; the rest remain visible as previews.
const LEGACY_PREVIEW_ONLY = [
  { id: "cream", label: "Kem (tùy chỉnh)", color: "#F9EDDB" },
  { id: "gray", label: "Xám nhạt (tùy chỉnh)", color: "#DCE2E9" },
  { id: "navy-legacy", label: "Navy cũ (tùy chỉnh)", color: "#294362" },
  { id: "pink", label: "Hồng nhạt (tùy chỉnh)", color: "#EBBDC9" },
] as const;
const DESIGN_BACKGROUNDS = [
  { id: "transparent", label: "Nền caro (mặc định)", color: null },
  ...C1717_SMS_COLORS,
  ...LEGACY_PREVIEW_ONLY,
] as const;
type DesignBackground = (typeof DESIGN_BACKGROUNDS)[number]["id"];
type PreviewDesign = NonNullable<Listing["designs"]>[number];
const C1717_SUPPLIER_IDS = new Set<string>(C1717_SMS_COLORS.filter(color => !color.setUp).map(color => color.id));
const C1717_NO_HEX_IDS = new Set<string>(C1717_SMS_COLORS.filter(color => !color.color).map(color => color.id));
const GARMENT_COLOR_IDS = new Set<string>(
  C1717_SMS_COLORS.filter(color => color.setUp && color.color).map(color => color.id),
);
const COLOR_MATCH_VERSIONS = new Set([
  "colormatch-pixel-v1", "colormatch-pixel-v2", "colormatch-pixel-v3",
  "colormatch-sms-c1717-v4", "colormatch-sms-c1717-v5", "colormatch-sms-c1717-v6", "colormatch-sms-c1717-v7", "colormatch-sms-c1717-v8", "colormatch-sms-c1717-v9", "colormatch-sms-c1717-v10",
]);
const COLOR_OVERRIDE_KEY = "cam-etsy-colormatch-choice:";

// The lower palette is visually ordered (neutrals, then hue) rather than the
// arbitrary SMS catalog order. It never influences recommendation ranking.
function paletteVisualOrder(color: string | null): number {
  if (!color) return 1000; // unknown HEX stays at the end
  const r = parseInt(color.slice(1, 3), 16) / 255;
  const g = parseInt(color.slice(3, 5), 16) / 255;
  const b = parseInt(color.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  if (!delta || delta / Math.max(max, 0.01) < 0.17) return 1 - max;
  let hue = max === r ? ((g - b) / delta) % 6
    : max === g ? (b - r) / delta + 2
      : (r - g) / delta + 4;
  hue = ((hue * 60) + 360) % 360;
  return 10 + hue + (1 - max);
}
function recommendedColors(design: PreviewDesign): DesignBackground[] {
  const match = design.color_match;
  if (!match || !COLOR_MATCH_VERSIONS.has(match.version) ||
      match.output_sha256 !== design.output_sha256 ||
      !Array.isArray(match.recommended)) return [];
  const seen = new Set<DesignBackground>();
  const qualityRank = match.version === "colormatch-sms-c1717-v8" &&
    Array.isArray(match.eligible_colors) ? match.eligible_colors : match.recommended;
  const strict = ["colormatch-sms-c1717-v8", "colormatch-sms-c1717-v9", "colormatch-sms-c1717-v10"].includes(match.version);
  const approved = strict ? new Set(match.eligible_colors ?? []) : null;
  const unsafe = new Set(match.unsafe_colors ?? []);
  const review = new Set(match.review_colors ?? []);
  const supportsReview = match.version === "colormatch-sms-c1717-v10";
  return qualityRank.filter((id): id is DesignBackground => {
    if (!GARMENT_COLOR_IDS.has(id as DesignBackground) ||
        seen.has(id as DesignBackground) || unsafe.has(id) ||
        (review.has(id) && !supportsReview) ||
        (approved && !approved.has(id) && !(supportsReview && review.has(id)))) return false;
    seen.add(id as DesignBackground);
    return true;
  }).slice(0, 10);
}
function designChoiceKey(design: PreviewDesign): string {
  return `${COLOR_OVERRIDE_KEY}${design.job_id}:${design.id}:${design.output_sha256}`;
}
function savedDesignColor(key: string): DesignBackground | null {
  try {
    const found = window.sessionStorage.getItem(key) as DesignBackground | null;
    return found && DESIGN_BACKGROUNDS.some(option => option.id === found) ? found : null;
  } catch {
    return null;
  }
}

const POSITION_LABELS: Record<string, string> = {
  front: "Mặt trước",
  back: "Mặt sau",
  left_sleeve: "Tay trái",
  right_sleeve: "Tay phải",
  sleeve: "Ống tay",
  chest: "Ngực áo",
  left_chest: "Ngực trái",
  right_chest: "Ngực phải",
  pocket: "Túi áo",
  hood: "Mũ áo",
  unknown: "Chưa rõ vị trí",
};
const POSITION_ORDER = [
  "front", "back", "left_chest", "right_chest", "chest",
  "left_sleeve", "right_sleeve", "sleeve", "pocket", "hood",
] as const;

function positionKey(value: string) {
  return String(value || "unknown").trim().toLowerCase().replace(/[\s-]+/g, "_");
}
function positionLabel(key: string) {
  return POSITION_LABELS[key] ?? key.replace(/_/g, " ").slice(0, 36);
}
function inventoryKindLabel(design?: DesignAsset) {
  const kind = design?.inventory_v3?.listing_type;
  return kind ? ({
    couple_set: "Couple", family_set: "Family", two_sided: "In hai mặt",
    repeated_view: "Nhiều góc chụp", multiple_variants: "Nhiều mẫu",
    multiple_placements: "Nhiều vị trí", single_design: "Một design",
    mixed: "Nhiều nhóm áo", uncertain: "Chưa đủ bằng chứng",
  } as Record<string, string>)[kind] : undefined;
}
function variantLabel(value: number, ordinal: number, design?: DesignAsset) {
  const meta = design?.inventory_v3;
  const variant = Number.isInteger(value) && value > 0 ? value : ordinal;
  if (meta?.set_id && /^G[0-9]{2}$/.test(meta.garment_group_id)) {
    const group = Number(meta.garment_group_id.slice(1));
    const label = group >= 1 && group <= 26 ? String.fromCharCode(64 + group) : meta.garment_group_id.slice(1);
    return `Áo ${label} · Mẫu ${variant}`;
  }
  return `Mẫu ${variant}`;
}

export function DesignCell({ item, onPreviewStart, canRetry = false, onRetryQueued }: {
  item: Listing; onPreviewStart?: () => void;
  canRetry?: boolean; onRetryQueued?: () => void;
}) {
  const designs = Array.isArray(item.designs) ? item.designs : [];
  const [active, setActive] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [failedOriginalId, setFailedOriginalId] = useState<string | null>(null);
  const [background, setBackground] = useState<DesignBackground>("transparent");
  const [colorOverrides, setColorOverrides] = useState<Record<string, DesignBackground>>({});
  const [productScope, setProductScope] = useState(false);
  const [productColor, setProductColor] = useState<DesignBackground | null>(null);
  const retryLockRef = useRef(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [retryQueued, setRetryQueued] = useState<{
    jobId: string; position: string; variant: number;
  } | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const galleryRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLButtonElement | null>(null);
  const galleryCountRef = useRef(designs.length);
  galleryCountRef.current = designs.length;
  const galleryOpen = active !== null && designs.length > 0;

  useEffect(() => {
    if (!galleryOpen) return;
    const previousOverflow = document.body.style.overflow;
    const returnFocus = returnFocusRef.current;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setActive(null);
      } else if (event.key === "Tab") {
        const buttons = Array.from(galleryRef.current?.querySelectorAll<HTMLButtonElement>(
          "button:not([disabled])",
        ) || []);
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        const focused = document.activeElement;
        const outside = !galleryRef.current?.contains(focused);
        if (first && last && ((event.shiftKey && (focused === first || outside)) ||
          (!event.shiftKey && (focused === last || outside)))) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      } else if (event.key === "ArrowRight" && galleryCountRef.current > 1) {
        event.preventDefault();
        setActive(index => index === null ? null : (index + 1) % galleryCountRef.current);
      } else if (event.key === "ArrowLeft" && galleryCountRef.current > 1) {
        event.preventDefault();
        setActive(index => index === null ? null :
          (index - 1 + galleryCountRef.current) % galleryCountRef.current);
      }
    };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, [galleryOpen]);

  useEffect(() => {
    if (designs.length === 0) setActive(null);
  }, [designs.length]);

  // Keep the dialog open while Picked polls and switch to the first result
  // for the originally selected print position when the new job has outputs.
  useEffect(() => {
    if (active === null || !retryQueued) return;
    const newPosition = designs.findIndex(asset =>
      asset.job_id === retryQueued.jobId &&
      positionKey(asset.position) === retryQueued.position &&
      asset.variant === retryQueued.variant);
    const firstNew = newPosition >= 0 ? newPosition
      : designs.findIndex(asset => asset.job_id === retryQueued.jobId);
    if (firstNew >= 0 && designs[active]?.job_id !== retryQueued.jobId)
      setActive(firstNew);
  }, [active, designs, retryQueued]);

  // Match the Etsy thumbnail preview: viewport-anchored, bottom-left, with
  // original Design PNG loaded only when hovered/focused. Touch opens gallery.
  useEffect(() => {
    if (hovered === null) return;
    const hide = () => setHovered(null);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [hovered]);

  const showHover = (index: number) => {
    onPreviewStart?.(); // hide the Etsy-row tooltip underneath the Design tooltip
    setFailedOriginalId(null);
    setHovered(index);
  };

  // No job, QA or waiting-state text belongs in the Picked Design cell.
  if (designs.length === 0) {
    if (!item.picked) {
      const status = item.extraction?.status;
      const label = status === "processing" ? "Đang tách design"
        : status === "pending" || status === "retry" ? "Chờ tách"
        : status === "failed" ? "Tách lỗi"
        : status === "disabled" ? "Tạm dừng"
        : status === "needs_ai" ? "Đang chờ xử lý"
        : "Chưa tách";
      return <span className="etsy-muted" title={label}>{label}</span>;
    }
    return <span className="etsy-design-empty" aria-label="Chưa có ảnh thiết kế" />;
  }

  const selected = active === null ? 0 : Math.min(active, designs.length - 1);
  const preview = designs[selected];
  const retryHasNewDesign = !!retryQueued &&
    designs.some(design => design.job_id === retryQueued.jobId);
  const remoteBusy = ["pending", "retry", "processing"]
    .includes(item.extraction?.status ?? "");
  const retryAwaiting = remoteBusy || (!!retryQueued && !retryHasNewDesign &&
    item.extraction?.job_id !== retryQueued.jobId);
  const retryFailed = !!retryQueued && !retryAwaiting && !retryHasNewDesign;
  const retryDesign = async () => {
    if (!canRetry || retryLockRef.current || retryAwaiting) return;
    retryLockRef.current = true;
    setRetryBusy(true);
    setRetryError(null);
    try {
      const response = await spyFetch<{
        job_id: string; status: string; already_queued: boolean;
      }>("/listings/" + encodeURIComponent(item.listingKey) + "/extraction/retry", {
        method: "POST",
        body: JSON.stringify({
          job_id: preview.job_id,
          asset_id: preview.id,
          output_sha256: preview.output_sha256,
        }),
      });
      setRetryQueued({
        jobId: response.job_id,
        position: positionKey(preview.position),
        variant: preview.variant,
      });
      onRetryQueued?.();
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : "Không thể gửi job mới");
    } finally {
      retryLockRef.current = false;
      setRetryBusy(false);
    }
  };
  const hoveredDesign = hovered === null ? null : designs[hovered];
  const grouped = new Map<string, {
    key: string; label: string;
    entries: { design: (typeof designs)[number]; index: number }[];
  }>();
  designs.forEach((design, index) => {
    const key = positionKey(design.position);
    const existing = grouped.get(key);
    if (existing) existing.entries.push({design, index});
    else grouped.set(key, {key, label: positionLabel(key), entries: [{design, index}]});
  });
  const positions = Array.from(grouped.values()).sort((a, b) => {
    const aOrder = POSITION_ORDER.indexOf(a.key as (typeof POSITION_ORDER)[number]);
    const bOrder = POSITION_ORDER.indexOf(b.key as (typeof POSITION_ORDER)[number]);
    return (aOrder < 0 ? POSITION_ORDER.length : aOrder) -
      (bOrder < 0 ? POSITION_ORDER.length : bOrder);
  });
  const selectedPosition = grouped.get(positionKey(preview.position)) ?? positions[0];
  const selectedOrdinal = selectedPosition.entries.findIndex(entry => entry.index === selected) + 1;
  const selectedVariant = variantLabel(preview.variant, selectedOrdinal, preview);
  const colorKey = designChoiceKey(preview);
  const suggestions = recommendedColors(preview);
  const productAdvice = sharedGarmentColors(designs, selected);
  const wholeProductColors = productAdvice?.colors.filter(id =>
    GARMENT_COLOR_IDS.has(id)) ?? [];
  const wholeProductActive = productScope && !!wholeProductColors.length &&
    !productAdvice?.awaitingAnalysis;
  const hasReadabilityGuard = ["colormatch-sms-c1717-v6", "colormatch-sms-c1717-v7", "colormatch-sms-c1717-v8", "colormatch-sms-c1717-v9", "colormatch-sms-c1717-v10"]
    .includes(preview.color_match?.version ?? "") &&
    preview.color_match?.output_sha256 === preview.output_sha256;
  const unsafeColors = new Set(hasReadabilityGuard && Array.isArray(preview.color_match?.unsafe_colors)
    ? preview.color_match.unsafe_colors : []);
  const paletteSuggestions = wholeProductActive ? wholeProductColors.slice(0, 10) : suggestions;
  const selectedColor = wholeProductActive
    ? ((productColor && wholeProductColors.includes(productColor) ? productColor :
        wholeProductColors[0]) as DesignBackground)
    : suggestions.length
      ? (colorOverrides[colorKey] ?? savedDesignColor(colorKey) ?? suggestions[0])
      : background;
  const selectedBackground = DESIGN_BACKGROUNDS.find(option => option.id === selectedColor)
    ?? DESIGN_BACKGROUNDS[0];
  const reviewColors = new Set(["colormatch-sms-c1717-v8", "colormatch-sms-c1717-v10"].includes(
    preview.color_match?.version ?? "") ? preview.color_match?.review_colors ?? [] : []);
  const reviewPreviewCount = paletteSuggestions.filter(id => reviewColors.has(id)).length;
  const chooseColor = (id: DesignBackground) => {
    if (wholeProductActive && wholeProductColors.includes(id)) {
      setProductColor(id);
      return;
    }
    if (wholeProductActive) setProductScope(false);
    if (suggestions.length) {
      setColorOverrides(current => ({...current, [colorKey]: id}));
      try { window.sessionStorage.setItem(colorKey, id); } catch { /* storage optional */ }
    } else {
      setBackground(id);
    }
  };
  const suggestedOptions = paletteSuggestions.map(id =>
    DESIGN_BACKGROUNDS.find(color => color.id === id),
  ).filter((color): color is (typeof DESIGN_BACKGROUNDS)[number] => Boolean(color));
  // A color is present in exactly one place: suggested OR remaining.
  const remainingOptions = DESIGN_BACKGROUNDS.slice(1)
    .filter(option => !paletteSuggestions.includes(option.id))
    .sort((a, b) => paletteVisualOrder(a.color) - paletteVisualOrder(b.color)
      || a.label.localeCompare(b.label));
  const renderSwatch = (option: (typeof DESIGN_BACKGROUNDS)[number]) => (
    <button key={option.id} type="button"
      className={`etsy-design-gallery-background-swatch etsy-design-gallery-background-${option.id}`}
      title={C1717_NO_HEX_IDS.has(option.id)
        ? `${option.label} · SMS chưa có HEX`
        : C1717_SUPPLIER_IDS.has(option.id)
          ? `${option.label} · Chỉ có tại supplier (chưa thiết lập trong SMS)`
          : reviewColors.has(option.id)
          ? `${option.label} · Màu xem thử, cần kiểm tra chữ và bản in mẫu`
          : unsafeColors.has(option.id)
            ? `${option.label} · Tương phản thấp, chỉ xem thử`
            : option.label}
      data-readability-risk={unsafeColors.has(option.id) ? "true" : undefined}
      data-review-preview={reviewColors.has(option.id) ? "true" : undefined}
      aria-label={C1717_NO_HEX_IDS.has(option.id)
        ? `${option.label} · SMS chưa có mã HEX` : option.label}
      disabled={C1717_NO_HEX_IDS.has(option.id)}
      style={option.color ? { backgroundColor: option.color } : undefined}
      aria-pressed={selectedColor === option.id}
      onClick={() => chooseColor(option.id)} />
  );
  const goToDesign = (delta: number) => {
    setActive(index => index === null ? null : (index + delta + designs.length) % designs.length);
  };

  return <>
    <div className="etsy-design-thumbnails"
      aria-label={`Ảnh thiết kế sản phẩm ${item.listingKey}`}>
      {designs.slice(0, 3).map((design, index) => (
        <button key={design.job_id + ":" + design.id} type="button"
          className="etsy-design-thumb"
          onPointerEnter={event => {
            if (event.pointerType !== "touch") showHover(index);
          }}
          onPointerLeave={() => setHovered(null)}
          onPointerCancel={() => setHovered(null)}
          onFocus={() => showHover(index)}
          onBlur={() => setHovered(null)}
          onClick={event => {
            returnFocusRef.current = event.currentTarget;
            setHovered(null);
            setBackground("transparent");
            setActive(index);
          }}
          aria-label={`Xem thiết kế ${index + 1} trong ${designs.length} ảnh`}>
          <EtsyDeferredImage src={design.thumbnail_url} alt="" />
        </button>
      ))}
    </div>

    {hoveredDesign && active === null && createPortal(
      <div className="etsy-product-hover-preview etsy-design-hover-preview"
        role="tooltip" data-listing-key={item.listingKey}
        data-design-index={hovered}>
        <span className="etsy-product-preview-label">Ảnh Design {hovered! + 1}/{designs.length}{inventoryKindLabel(hoveredDesign) ? ` · ${inventoryKindLabel(hoveredDesign)}` : ""}{hoveredDesign.inventory_v3 ? ` · ${positionLabel(positionKey(hoveredDesign.position))}` : ""}</span>
        <img src={failedOriginalId === hoveredDesign.id
          ? hoveredDesign.thumbnail_url : hoveredDesign.original_url}
          alt={`Thiết kế ${hovered! + 1} của ${item.pageTitle}`}
          decoding="async"
          onError={() => {
            if (failedOriginalId !== hoveredDesign.id) setFailedOriginalId(hoveredDesign.id);
            else setHovered(null);
          }} />
        <span className="etsy-product-preview-title">
          <span className="etsy-product-preview-title-text">{item.pageTitle}</span>
        </span>
      </div>, document.body
    )}

    {active !== null && createPortal(
      <div className="etsy-design-gallery-backdrop"
        onMouseDown={event => {
          if (event.currentTarget === event.target) setActive(null);
        }}>
        <section ref={galleryRef} role="dialog" aria-modal="true"
          aria-label={`Ảnh thiết kế sản phẩm ${item.listingKey}`}
          className="etsy-design-gallery" data-preview-background={selectedColor}
          style={{ "--etsy-gallery-bg": selectedBackground.color ?? "#fff" } as CSSProperties}>
          <header className="etsy-design-gallery-header">
            <div className="etsy-design-gallery-heading">
              <span className="etsy-design-gallery-eyebrow">DESIGN PREVIEW{inventoryKindLabel(preview) ? ` · ${inventoryKindLabel(preview)}` : ""}</span>
              <h2>Thiết kế theo vị trí in</h2>
              <p title={item.pageTitle}>{item.pageTitle}</p>
            </div>
            <div className="etsy-design-gallery-header-meta" aria-label="Số lượng ảnh">
              <strong>{designs.length}</strong> thiết kế
              <span aria-hidden="true">·</span>
              <strong>{positions.length}</strong> vị trí
            </div>
            {canRetry && <button type="button"
              className="etsy-design-gallery-extraction-retry"
              onClick={() => { void retryDesign(); }}
              disabled={retryBusy || retryAwaiting}
              title="Tách lại design từ ảnh Etsy gốc bằng luồng Image Agent hiện tại; không xóa ảnh cũ"
              aria-label="Tạo lại design từ ảnh gốc">
              <span aria-hidden="true" className={retryBusy ? "is-spinning" : ""}>↻</span>
              <span>{retryBusy ? "Đang gửi…" :
                retryAwaiting ? "Đang tách lại" : "Tạo lại design"}</span>
            </button>}
            <button ref={closeRef} className="etsy-design-gallery-close" type="button"
              onClick={() => setActive(null)} aria-label="Đóng">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 5L19 19M19 5L5 19" />
              </svg>
            </button>
          </header>
          {retryError && <p className="etsy-design-gallery-retry-feedback is-error" role="alert">
            Không thể tạo lại design: {retryError}. Ảnh hiện tại vẫn được giữ nguyên.
          </p>}
          {!retryError && retryQueued && <p className="etsy-design-gallery-retry-feedback"
            role="status" aria-live="polite">
            {retryHasNewDesign
              ? "Đã nhận design mới từ Image Agent · cần kiểm tra trước khi in"
              : retryFailed
                ? "Lần tách mới chưa có ảnh hợp lệ · ảnh cũ vẫn được giữ"
                : "Đã xếp hàng tách design bằng Image Agent · ảnh cũ vẫn hiển thị"}
          </p>}
          <div className="etsy-design-gallery-main">
            <div className="etsy-design-gallery-viewer">
              <DesignZoomStage key={preview.job_id + ":" + preview.id + ":" + preview.output_sha256}
                originalUrl={preview.original_url}
                thumbnailUrl={preview.thumbnail_url}
                alt={`Thiết kế ${selectedVariant} - ${selectedPosition.label}`}
                positionLabel={selectedPosition.label} variantLabel={selectedVariant} />
              <footer className="etsy-design-gallery-viewer-footer">
                <div className="etsy-design-gallery-current" aria-live="polite">
                  <strong>{selectedPosition.label} · {selectedVariant}</strong>
                  <span>{selected + 1} / {designs.length} thiết kế</span>
                </div>
                <div className="etsy-design-gallery-navigation" aria-label="Chuyển ảnh">
                  <button type="button" aria-label="Thiết kế trước" title="Thiết kế trước (←)"
                    onClick={() => goToDesign(-1)} disabled={designs.length === 1}>
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M15 5L8 12L15 19" />
                    </svg>
                  </button>
                  <button type="button" aria-label="Thiết kế tiếp theo" title="Thiết kế tiếp theo (→)"
                    onClick={() => goToDesign(1)} disabled={designs.length === 1}>
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M9 5L16 12L9 19" />
                    </svg>
                  </button>
                </div>
              </footer>
            </div>
            <aside className="etsy-design-gallery-side" aria-label="Vị trí và ảnh thiết kế">
              <section className="etsy-design-gallery-side-section">
                <div className="etsy-design-gallery-section-heading">
                  <strong>Vị trí in</strong>
                  <span>{positions.length} vị trí</span>
                </div>
                <div className="etsy-design-gallery-position-list" role="group"
                  aria-label="Chọn vị trí in">
                  {positions.map(position => (
                    <button key={position.key} type="button"
                      className={position.key === selectedPosition.key ? "active" : ""}
                      onClick={() => { setActive(position.entries[0].index); }}
                      aria-pressed={position.key === selectedPosition.key}
                      aria-label={`${position.label}, ${position.entries.length} thiết kế`}>
                      <span>{position.label}</span>
                      <span className="etsy-design-gallery-position-count">
                        {position.entries.length}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              <section className="etsy-design-gallery-side-section etsy-design-gallery-variants">
                <div className="etsy-design-gallery-list" role="group"
                  aria-label={`Các thiết kế ${selectedPosition.label}`}>
                  {selectedPosition.entries.map((entry, ordinal) => (
                    <button key={entry.design.job_id + ":" + entry.design.id} type="button"
                      className={entry.index === selected ? "active" : ""}
                      onClick={() => { setActive(entry.index); }}
                      aria-label={`Xem thiết kế ${entry.index + 1}, ${selectedPosition.label}, ${variantLabel(entry.design.variant, ordinal + 1, entry.design)}`}
                      aria-pressed={entry.index === selected}>
                      <span className="etsy-design-gallery-variant-artwork">
                        <EtsyDeferredImage src={entry.design.thumbnail_url} alt=""
                          width={88} height={88} />
                      </span>
                      <span className="etsy-design-gallery-variant-label">
                        {variantLabel(entry.design.variant, ordinal + 1, entry.design)}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              <section className="etsy-design-gallery-side-section etsy-design-gallery-backgrounds">
                <div className="etsy-design-gallery-section-heading">
                  <strong>Nền xem thử</strong>
                  <span>{selectedBackground.label}</span>
                </div>
                <div className="etsy-design-gallery-clear-background"
                  role="group" aria-label="Nền trong suốt">
                  <span>Nền trong suốt</span>
                  {renderSwatch(DESIGN_BACKGROUNDS[0])}
                </div>
                {productAdvice && <label className="etsy-colormatch-product-toggle etsy-design-gallery-product-toggle"
                  title={productAdvice.awaitingAnalysis
                    ? "Chưa phân tích đủ các vị trí in" : undefined}>
                  <input type="checkbox" checked={wholeProductActive}
                    disabled={productAdvice.awaitingAnalysis || !wholeProductColors.length}
                    onChange={event => setProductScope(event.target.checked)} />
                  Dùng chung một màu cho {productAdvice.positions.length} vị trí in
                </label>}
                <div className="etsy-design-gallery-palette-section">
                  <div className="etsy-design-gallery-colormatch-heading">
                    <strong>{suggestedOptions.length && reviewPreviewCount === suggestedOptions.length
                      ? "Màu nên xem thử" : "Màu phù hợp nhất"}</strong>
                    <small>{suggestedOptions.length} màu được gợi ý</small>
                  </div>
                  {suggestedOptions.length ? <div
                    className="etsy-design-gallery-background-picker etsy-design-gallery-recommended"
                    role="group" aria-label="Màu phù hợp với thiết kế">
                    {suggestedOptions.map(renderSwatch)}
                  </div> : <p className="etsy-design-gallery-palette-empty">
                    {hasReadabilityGuard
                      ? "Không có màu đạt mức đề xuất; hãy chọn màu bên dưới để xem thử."
                      : "Chưa có dữ liệu gợi ý phù hợp cho thiết kế này."}
                  </p>}
                  {reviewPreviewCount > 0 && <p className="etsy-design-gallery-palette-note">
                    {reviewPreviewCount} màu để xem thử · cần kiểm tra chữ và bản in mẫu
                  </p>}
                </div>
                <div className="etsy-design-gallery-palette-section etsy-design-gallery-remaining">
                  <div className="etsy-design-gallery-colormatch-heading">
                    <strong>Các màu còn lại</strong>
                    <small>{remainingOptions.length} màu</small>
                  </div>
                  <div className="etsy-design-gallery-background-picker etsy-design-gallery-other-colors"
                    role="group" aria-label="Các màu còn lại">
                    {remainingOptions.map(renderSwatch)}
                  </div>
                </div>
              </section>
            </aside>
          </div>
        </section>
      </div>, document.body
    )}
  </>;
}