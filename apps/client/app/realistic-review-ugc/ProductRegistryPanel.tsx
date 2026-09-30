import { useEffect, useMemo, useState } from "react";
import {
  archiveProduct,
  archiveProductReference,
  createProduct,
  importProductUrls,
  listProductReferences,
  listProducts,
  updateProduct,
  uploadProductReference,
} from "./api";
import type {
  Product,
  ProductCreateRequest,
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

function variantSummary(variant: Record<string, unknown>, index: number): string {
  const parts = ["name", "color", "size", "sku"]
    .map(key => typeof variant[key] === "string" ? String(variant[key]).trim() : "")
    .filter(Boolean);
  return parts.join(" · ") || "Variant " + (index + 1);
}

export function ProductRegistryPanel() {
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [references, setReferences] = useState<ProductReference[]>([]);
  const [productUrls, setProductUrls] = useState("");
  const [urlImportResult, setUrlImportResult] = useState<ProductUrlImportResult | null>(null);
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [color, setColor] = useState("");
  const [material, setMaterial] = useState("");
  const [crownProfile, setCrownProfile] = useState("mid");
  const [crownHeight, setCrownHeight] = useState("");
  const [brimStyle, setBrimStyle] = useState("curved");
  const [brimLength, setBrimLength] = useState("");
  const [circumference, setCircumference] = useState("");
  const [logoPosition, setLogoPosition] = useState("front center");
  const [fitNotes, setFitNotes] = useState("");
  const [viewType, setViewType] = useState<ProductReferenceView>("front");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const selected = products.find(product => product.id === selectedId) || null;
  const latestByView = useMemo(() => {
    const values = new Map<ProductReferenceView, ProductReference>();
    for (const reference of references) {
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

  async function submitProduct() {
    if (!sku.trim() || !name.trim() || busy) return;
    setBusy("create");
    setError("");
    const payload: ProductCreateRequest = {
      sku: sku.trim(),
      name: name.trim(),
      product_type: "hat",
      color: color.trim() || undefined,
      material: material.trim() || undefined,
      crown_profile: crownProfile.trim() || undefined,
      crown_height_mm: crownHeight ? Number(crownHeight) : undefined,
      brim_style: brimStyle.trim() || undefined,
      brim_length_mm: brimLength ? Number(brimLength) : undefined,
      circumference_mm: circumference ? Number(circumference) : undefined,
      logo_position: logoPosition.trim() || undefined,
      fit_notes: fitNotes.trim() || undefined,
    };
    try {
      const created = await createProduct(payload);
      await refreshProducts();
      setSelectedId(created.id);
      setSku("");
      setName("");
      setColor("");
      setMaterial("");
      setCrownHeight("");
      setBrimLength("");
      setCircumference("");
      setFitNotes("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create product.");
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

    <section className="rrugc-product-url-import" aria-label="Import products from URLs">
      <div>
        <small>PRODUCT URL IMPORT</small>
        <h3>Scan product pages</h3>
        <p>Paste one product URL per line. The system extracts product details, variants and gallery images, then saves the primary image as the front reference when possible.</p>
      </div>
      <textarea
        rows={3}
        value={productUrls}
        onChange={event => setProductUrls(event.target.value)}
        placeholder={"https://store.example.com/products/product-a\nhttps://store.example.com/products/product-b"}
        aria-label="Product URLs"
      />
      <div className="rrugc-product-url-actions">
        <span>{productUrlsFromText(productUrls).length}/10 URLs</span>
        <button
          type="button"
          className="rrugc-primary"
          disabled={!productUrlsFromText(productUrls).length || Boolean(busy)}
          onClick={() => void scanProductUrls()}
        >
          {busy === "url-import" ? "Scanning product pages…" : "Scan & import products"}
        </button>
      </div>
      {urlImportResult && <div className="rrugc-product-import-result">
        <strong>{urlImportResult.created} created · {urlImportResult.updated} updated · {urlImportResult.failed} failed</strong>
        {urlImportResult.items.map((item, index) => <div key={item.source_url + index} className={"is-" + item.status}>
          <span>{item.product?.name || item.source_url}</span>
          <small>
            {item.status === "failed"
              ? item.error_message || item.error_code || "Import failed"
              : item.images_found + " images found"
                + (item.primary_reference_imported ? " · front reference saved" : "")
                + (item.warning ? " · " + item.warning : "")}
          </small>
        </div>)}
      </div>}
    </section>

    <div className="rrugc-product-layout">
      <details className="rrugc-product-create" open={products.length === 0}>
        <summary><span><strong>Manual product fallback</strong><small>Use only when a product page cannot be scanned.</small></span><b>Manual SKU</b></summary>
        <div className="rrugc-product-form-grid">
          <label>SKU<input value={sku} onChange={event => setSku(event.target.value)} placeholder="CAP-001" /></label>
          <label>Name<input value={name} onChange={event => setName(event.target.value)} placeholder="Forest Green Cap" /></label>
          <label>Color<input value={color} onChange={event => setColor(event.target.value)} placeholder="forest green" /></label>
          <label>Material<input value={material} onChange={event => setMaterial(event.target.value)} placeholder="cotton twill" /></label>
          <label>Crown profile<input value={crownProfile} onChange={event => setCrownProfile(event.target.value)} placeholder="mid" /></label>
          <label>Crown height mm<input type="number" min="1" value={crownHeight} onChange={event => setCrownHeight(event.target.value)} placeholder="118" /></label>
          <label>Brim style<input value={brimStyle} onChange={event => setBrimStyle(event.target.value)} placeholder="curved" /></label>
          <label>Brim length mm<input type="number" min="1" value={brimLength} onChange={event => setBrimLength(event.target.value)} placeholder="72" /></label>
          <label>Circumference mm<input type="number" min="1" value={circumference} onChange={event => setCircumference(event.target.value)} placeholder="580" /></label>
          <label>Logo position<input value={logoPosition} onChange={event => setLogoPosition(event.target.value)} placeholder="front center" /></label>
        </div>
        <label>Fit / geometry notes<textarea value={fitNotes} onChange={event => setFitNotes(event.target.value)} rows={3} placeholder="Structured six-panel cap, preserve crown depth and brim curvature." /></label>
        <button type="button" className="rrugc-primary" disabled={!sku.trim() || !name.trim() || Boolean(busy)} onClick={() => void submitProduct()}>
          {busy === "create" ? "Creating…" : "Create product"}
        </button>
      </details>

      <div className="rrugc-product-list">
        <h3>Product library</h3>
        {products.length === 0 ? <p className="rrugc-empty">No product SKU yet.</p> : products.map(product =>
          <button type="button" key={product.id} className={product.id === selectedId ? "active" : ""} onClick={() => setSelectedId(product.id)}>
            <span><strong>{product.sku}</strong><small>{product.name}</small></span>
            <span><b>{product.reference_count}</b><small>refs</small></span>
            <span className="rrugc-product-view-count"><b>{product.active_views.length}</b><small>views</small></span>
          </button>
        )}
      </div>
    </div>

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
          <span>Variants <b>{selected.source_variants.length}</b></span>
          {selected.source_fetched_at && <span>Scanned <b>{new Date(selected.source_fetched_at).toLocaleString()}</b></span>}
        </div>
        {selected.source_description && <p>{selected.source_description}</p>}
        {selected.source_images.length > 0 && <div className="rrugc-source-gallery">
          {selected.source_images.slice(0, 10).map((imageUrl, index) => <a key={imageUrl} href={imageUrl} target="_blank" rel="noreferrer" title={"Product image " + (index + 1)}>
            <img src={imageUrl} alt={"Product source " + (index + 1)} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
          </a>)}
        </div>}
        {selected.source_variants.length > 0 && <div className="rrugc-source-variants">
          {selected.source_variants.slice(0, 8).map((variant, index) => <span key={index}>{variantSummary(variant, index)}</span>)}
          {selected.source_variants.length > 8 && <small>+{selected.source_variants.length - 8} more variants</small>}
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
