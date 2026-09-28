import { useEffect, useMemo, useState } from "react";
import {
  archiveProduct,
  archiveProductReference,
  createProduct,
  listProductReferences,
  listProducts,
  updateProduct,
  uploadProductReference,
} from "./api";
import type {
  Product,
  ProductCreateRequest,
  ProductReference,
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

export function ProductRegistryPanel() {
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [references, setReferences] = useState<ProductReference[]>([]);
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

  return <section className="rrugc-card rrugc-product-registry">
    <div className="rrugc-section-heading">
      <div>
        <small>PRODUCT GROUNDING</small>
        <h2>Product reference library</h2>
        <p>Keep SKU geometry and reference views in one source of truth for consistent downstream generation.</p>
      </div>
      <span className="rrugc-safe-badge">{products.length} active SKU{products.length === 1 ? "" : "s"}</span>
    </div>

    {error && <div className="rrugc-error" role="alert">{error}</div>}

    <div className="rrugc-product-layout">
      <details className="rrugc-product-create" open={products.length === 0}>
        <summary><span><strong>Add product SKU</strong><small>Create a new product truth record only when needed.</small></span><b>New SKU</b></summary>
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
              <img className="rrugc-product-reference-preview" src={referenceImageUrl(selected.id, reference.id)} alt={viewLabel(view) + " product reference"} loading="lazy" />
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
  </section>;
}
