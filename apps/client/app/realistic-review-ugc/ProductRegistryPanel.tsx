import { useEffect, useMemo, useState } from "react";
import {
  archiveProduct,
  archiveProductReference,
  importProductUrls,
  listProductReferences,
  listProducts,
  updateProduct,
  updateProductVariant,
  uploadProductReference,
} from "./api";
import type {
  Product,
  ProductReference,
  ProductUrlImportResult,
  ProductReferenceView,
} from "./types";

const PRODUCT_VIEWS: ProductReferenceView[] = [
  "front",
  "front_45_left",
  "front_45_right",
  "side_left",
  "side_right",
  "back",
  "top",
  "logo_closeup",
  "embroidery_closeup",
  "material_closeup",
];

const viewLabel = (value: ProductReferenceView) =>
  value.replaceAll("_", " ").replace(/\b45\b/, "45°");

const shortHash = (value: string) => value.slice(0, 10) + "…";
const referenceImageUrl = (productId: string, referenceId: string) =>
  "/api/v1/realistic-review-ugc/products/" + encodeURIComponent(productId)
  + "/references/" + encodeURIComponent(referenceId) + "/image";

export function productUrlsFromText(value: string): string[] {
  const seen = new Set<string>();
  const urls: string[] = [];
  for (const raw of value.split(/[\n,]+/)) {
    const url = raw.trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    urls.push(url);
    if (urls.length >= 10) break;
  }
  return urls;
}

export function ProductRegistryPanel() {
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [references, setReferences] = useState<ProductReference[]>([]);
  const [productUrls, setProductUrls] = useState("");
  const [urlImportResult, setUrlImportResult] = useState<ProductUrlImportResult | null>(null);
  const [viewType, setViewType] = useState<ProductReferenceView>("front");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const selected = products.find(product => product.id === selectedId) || null;
  const latestByView = useMemo(() => {
    const values = new Map<ProductReferenceView, ProductReference>();
    for (const reference of references) {
      if (reference.variant_id) continue;
      if (!values.has(reference.view_type)) values.set(reference.view_type, reference);
    }
    return values;
  }, [references]);

  async function refreshProducts(signal?: AbortSignal) {
    const rows = await listProducts(signal);
    setProducts(rows);
    setSelectedId(current => current && rows.some(row => row.id === current) ? current : rows[0]?.id || "");
  }

  async function refreshReferences(productId: string, signal?: AbortSignal) {
    setReferences(await listProductReferences(productId, signal));
  }

  useEffect(() => {
    const controller = new AbortController();
    void refreshProducts(controller.signal).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load products.");
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setReferences([]);
      return;
    }
    const controller = new AbortController();
    void refreshReferences(selectedId, controller.signal).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load product references.");
    });
    return () => controller.abort();
  }, [selectedId]);

  async function scanProductUrls() {
    const urls = productUrlsFromText(productUrls);
    if (!urls.length || busy) return;
    setBusy("url-import");
    setError("");
    setUrlImportResult(null);
    try {
      const result = await importProductUrls(urls, true);
      setUrlImportResult(result);
      await refreshProducts();
      const first = result.items.find(item => item.product)?.product;
      if (first) setSelectedId(first.id);
      if (result.failed === 0) setProductUrls("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to scan product URLs.");
    } finally {
      setBusy("");
    }
  }

  async function saveSelectedGeometry() {
    if (!selected || busy) return;
    setBusy("edit-" + selected.id);
    setError("");
    try {
      const updated = await updateProduct(selected.id, {
        color: selected.color,
        material: selected.material,
        crown_profile: selected.crown_profile,
        crown_height_mm: selected.crown_height_mm,
        brim_style: selected.brim_style,
        brim_length_mm: selected.brim_length_mm,
        circumference_mm: selected.circumference_mm,
        logo_position: selected.logo_position,
        fit_notes: selected.fit_notes,
      });
      setProducts(rows => rows.map(row => row.id === updated.id ? updated : row));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update product.");
    } finally {
      setBusy("");
    }
  }

  function patchSelected(patch: Partial<Product>) {
    if (!selected) return;
    setProducts(rows => rows.map(row => row.id === selected.id ? { ...row, ...patch } : row));
  }

  async function uploadReference() {
    if (!selected || !uploadFile || busy) return;
    setBusy("upload");
    setError("");
    try {
      await uploadProductReference(selected.id, viewType, uploadFile);
      setUploadFile(null);
      await Promise.all([refreshReferences(selected.id), refreshProducts()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to upload reference.");
    } finally {
      setBusy("");
    }
  }

  async function archiveReference(reference: ProductReference) {
    if (!selected || busy) return;
    setBusy(reference.id);
    setError("");
    try {
      await archiveProductReference(selected.id, reference.id);
      await Promise.all([refreshReferences(selected.id), refreshProducts()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to archive reference.");
    } finally {
      setBusy("");
    }
  }

  async function toggleVariant(variantId: string, enabled: boolean) {
    if (!selected || busy) return;
    setBusy("variant-" + variantId);
    setError("");
    try {
      const updated = await updateProductVariant(selected.id, variantId, enabled);
      setProducts(rows => rows.map(product => product.id === selected.id ? {
        ...product,
        revision: product.revision + 1,
        variants: product.variants.map(variant => variant.id === updated.id ? updated : variant),
      } : product));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update product color.");
    } finally {
      setBusy("");
    }
  }

  async function archiveSelected() {
    if (!selected || busy) return;
    setBusy("archive-" + selected.id);
    setError("");
    try {
      await archiveProduct(selected.id);
      await refreshProducts();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to archive product.");
    } finally {
      setBusy("");
    }
  }

  return <details className="rrugc-card rrugc-product-registry rrugc-compact-section">
    <summary className="rrugc-compact-section-summary">
      <span><small>PRODUCT GROUNDING</small><strong>Product reference library</strong></span>
      <b>{products.length} SKU{products.length === 1 ? "" : "s"} · Manage</b>
    </summary>

    {error && <div className="rrugc-error" role="alert">{error}</div>}

    <section className="rrugc-product-url-import" aria-label="Import product from URL">
      <div>
        <small>PRODUCT LINK</small>
        <h3>Add product reference</h3>
        <p>Paste the product page link. Product details, colors and gallery images are collected automatically.</p>
      </div>
      <input
        type="url"
        value={productUrls}
        onChange={event => setProductUrls(event.target.value)}
        placeholder="https://store.example.com/products/product"
        aria-label="Product URL"
        onKeyDown={event => {
          if (event.key === "Enter" && productUrlsFromText(productUrls).length && !busy) {
            event.preventDefault();
            void scanProductUrls();
          }
        }}
      />
      <div className="rrugc-product-url-actions">
        <button
          type="button"
          className="rrugc-primary"
          disabled={!productUrlsFromText(productUrls).length || Boolean(busy)}
          onClick={() => void scanProductUrls()}
        >
          {busy === "url-import" ? "Scanning product…" : "Scan & import"}
        </button>
      </div>
      {urlImportResult && <div className="rrugc-product-import-result">
        <strong>{urlImportResult.created} created · {urlImportResult.updated} updated · {urlImportResult.failed} failed</strong>
        {urlImportResult.items.map((item, index) => <div key={item.source_url + index} className={"is-" + item.status}>
          <span>{item.product?.name || item.source_url}</span>
          <small>
            {item.status === "failed"
              ? item.error_message || item.error_code || "Import failed"
              : item.images_found + " images"
                + " · " + item.variants_found + " color variant" + (item.variants_found === 1 ? "" : "s")
                + (item.variant_references_imported ? " · " + item.variant_references_imported + " variant refs saved" : "")
                + (item.primary_reference_imported ? " · parent front ref saved" : "")
                + (item.warning ? " · " + item.warning : "")}
          </small>
        </div>)}
      </div>}
    </section>

    {products.length > 0 && <div className="rrugc-product-layout rrugc-product-layout--links-only">
      <div className="rrugc-product-list">
        <h3>Imported products</h3>
        {products.map(product =>
          <button type="button" key={product.id} className={product.id === selectedId ? "active" : ""} onClick={() => setSelectedId(product.id)}>
            <span><strong>{product.sku}</strong><small>{product.name}</small></span>
            <span><b>{product.reference_count}</b><small>refs</small></span>
            <span className="rrugc-product-view-count"><b>{product.active_views.length}</b><small>views</small></span>
          </button>
        )}
      </div>
    </div>}

    {selected && <div className="rrugc-product-detail">
      <div className="rrugc-product-detail-head">
        <div><small>SELECTED PRODUCT</small><h3>{selected.sku} · {selected.name}</h3><p>Geometry revision {selected.revision}</p></div>
        <button type="button" className="rrugc-danger-ghost" disabled={Boolean(busy)} onClick={() => void archiveSelected()}>Archive SKU</button>
      </div>

      {selected.source_url && <section className="rrugc-source-product">
        <header>
          <div><small>SOURCE PRODUCT</small><strong>{selected.brand || selected.source_host || "Imported product"}</strong></div>
          <a href={selected.source_url} target="_blank" rel="noreferrer">Open product page</a>
        </header>
        <div className="rrugc-source-product-meta">
          {selected.source_category && <span>Category <b>{selected.source_category}</b></span>}
          {(selected.source_price_text || selected.source_currency) && <span>Price <b>{[selected.source_price_text, selected.source_currency].filter(Boolean).join(" ")}</b></span>}
          <span>Gallery <b>{selected.source_images.length}</b></span>
          <span>Color variants <b>{selected.variants.length}</b></span>
          {selected.source_fetched_at && <span>Scanned <b>{new Date(selected.source_fetched_at).toLocaleString()}</b></span>}
        </div>
        {selected.source_description && <p>{selected.source_description}</p>}
        {selected.source_images.length > 0 && <div className="rrugc-source-gallery">
          {selected.source_images.slice(0, 10).map((imageUrl, index) => <a key={imageUrl} href={imageUrl} target="_blank" rel="noreferrer" title={"Product image " + (index + 1)}>
            <img src={imageUrl} alt={"Product source " + (index + 1)} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
          </a>)}
        </div>}
        {selected.variants.length > 0 && <div className="rrugc-variant-grid">
          {selected.variants.map(variant => <article key={variant.id} className={variant.enabled ? "is-enabled" : "is-disabled"}>
            <div className="rrugc-variant-thumb">
              {variant.image_urls[0]
                ? <img src={variant.image_urls[0]} alt={variant.color || variant.name || "Product color"} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
                : <span>No image</span>}
            </div>
            <div className="rrugc-variant-copy">
              <strong>{variant.color || variant.name || "Variant"}</strong>
              <small>{[variant.sku, variant.size].filter(Boolean).join(" · ") || variant.source_variant_id}</small>
              <span>{variant.reference_count} ref{variant.reference_count === 1 ? "" : "s"}{variant.available ? "" : " · unavailable"}</span>
            </div>
            <label className="rrugc-variant-toggle">
              <input
                type="checkbox"
                checked={variant.enabled}
                disabled={Boolean(busy)}
                onChange={event => void toggleVariant(variant.id, event.target.checked)}
              />
              <span>{busy === "variant-" + variant.id ? "Saving…" : variant.enabled ? "Enabled" : "Disabled"}</span>
            </label>
          </article>)}
        </div>}
      </section>}

      <div className="rrugc-product-form-grid rrugc-product-edit-grid">
        <label>Color<input value={selected.color || ""} onChange={event => patchSelected({ color: event.target.value })} /></label>
        <label>Material<input value={selected.material || ""} onChange={event => patchSelected({ material: event.target.value })} /></label>
        <label>Crown profile<input value={selected.crown_profile || ""} onChange={event => patchSelected({ crown_profile: event.target.value })} /></label>
        <label>Crown height mm<input type="number" value={selected.crown_height_mm ?? ""} onChange={event => patchSelected({ crown_height_mm: event.target.value ? Number(event.target.value) : null })} /></label>
        <label>Brim style<input value={selected.brim_style || ""} onChange={event => patchSelected({ brim_style: event.target.value })} /></label>
        <label>Brim length mm<input type="number" value={selected.brim_length_mm ?? ""} onChange={event => patchSelected({ brim_length_mm: event.target.value ? Number(event.target.value) : null })} /></label>
        <label>Circumference mm<input type="number" value={selected.circumference_mm ?? ""} onChange={event => patchSelected({ circumference_mm: event.target.value ? Number(event.target.value) : null })} /></label>
        <label>Logo position<input value={selected.logo_position || ""} onChange={event => patchSelected({ logo_position: event.target.value })} /></label>
      </div>
      <label>Fit / geometry notes<textarea rows={2} value={selected.fit_notes || ""} onChange={event => patchSelected({ fit_notes: event.target.value })} /></label>
      <button type="button" className="rrugc-secondary-action rrugc-save-geometry" disabled={Boolean(busy)} onClick={() => void saveSelectedGeometry()}>
        {busy === "edit-" + selected.id ? "Saving…" : "Save geometry"}
      </button>

      <div className="rrugc-reference-upload">
        <label>Reference view
          <select value={viewType} onChange={event => setViewType(event.target.value as ProductReferenceView)}>
            {PRODUCT_VIEWS.map(view => <option key={view} value={view}>{viewLabel(view)}</option>)}
          </select>
        </label>
        <label className="rrugc-file-label">Image
          <input type="file" accept="image/jpeg,image/png,image/webp,image/avif,image/heif,image/heic" onChange={event => setUploadFile(event.target.files?.[0] || null)} />
          <small>{uploadFile ? uploadFile.name : "Up to 20 MB"}</small>
        </label>
        <button type="button" className="rrugc-primary" disabled={!uploadFile || Boolean(busy)} onClick={() => void uploadReference()}>
          {busy === "upload" ? "Uploading…" : "Upload version"}
        </button>
      </div>

      <div className="rrugc-reference-matrix">
        {PRODUCT_VIEWS.map(view => {
          const reference = latestByView.get(view);
          return <article key={view} className={reference ? "has-reference" : ""}>
            <header><strong>{viewLabel(view)}</strong>{reference && <span>v{reference.version}</span>}</header>
            {reference ? <>
              <div className="rrugc-image-shell rrugc-product-reference-shell">
                <span className="rrugc-image-skeleton" aria-hidden="true" />
                <img
                  className="rrugc-product-reference-preview"
                  src={referenceImageUrl(selected.id, reference.id)}
                  alt={viewLabel(view) + " product reference"}
                  loading="lazy"
                  decoding="async"
                  onLoad={event => event.currentTarget.parentElement?.classList.add("is-loaded")}
                  onError={event => event.currentTarget.parentElement?.classList.add("is-loaded")}
                />
              </div>
              <p>{reference.width}×{reference.height} · {reference.image_format}</p>
              <p title={reference.content_hash}>hash {shortHash(reference.content_hash)}</p>
              {reference.reused_storage && <small className="rrugc-reused-badge">Drive object reused</small>}
              <footer>
                {reference.web_url && <a href={reference.web_url} target="_blank" rel="noreferrer">Open Drive</a>}
                <button type="button" disabled={Boolean(busy)} onClick={() => void archiveReference(reference)}>Archive</button>
              </footer>
            </> : <p className="rrugc-reference-missing">Missing reference</p>}
          </article>;
        })}
      </div>

      {references.length > 0 && <details className="rrugc-reference-history">
        <summary>Version history <span>{references.length}</span></summary>
        <div>
          {references.map(reference => <p key={reference.id}>
            <strong>{viewLabel(reference.view_type)} v{reference.version}</strong>
            <span>{reference.original_filename || reference.image_format}</span>
            <code>{shortHash(reference.content_hash)}</code>
          </p>)}
        </div>
      </details>}
    </div>}
  </details>;
}
