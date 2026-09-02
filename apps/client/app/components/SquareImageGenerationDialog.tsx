import { useEffect, useMemo, useRef, useState } from "react";

export type ImageGenerationProvider = {
  id: "adobe_firefly" | "gemini";
  name: string;
  available: boolean;
  preservation_mode: "strict_expand" | "semantic_expand";
  recommended: boolean;
  model?: string | null;
};
export type ImageGenerationCapabilities = {
  enabled: boolean;
  operations: string[];
  target_sizes: Array<1024 | 2048>;
  providers: ImageGenerationProvider[];
};
export type ImageGeneration = {
  id: string;
  source_asset_id: string;
  status: "queued" | "preparing" | "submitted" | "running" | "storing" | "completed" | "failed" | "cancelled";
  provider: "adobe_firefly" | "gemini";
  model: string | null;
  preservation_mode: "strict_expand" | "semantic_expand";
  target_width: number;
  target_height: number;
  output_asset_id: string | null;
  error: { code: string; message: string } | null;
};

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const POLL_MS = 3_000;

export function generationStatusLabel(status: ImageGeneration["status"]): string {
  return ({
    queued: "Queued",
    preparing: "Preparing",
    submitted: "Generating",
    running: "Generating",
    storing: "Storing",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  })[status];
}

function messageFrom(payload: any, fallback: string): string {
  const detail = payload?.detail;
  return (typeof detail === "string" ? detail : detail?.message) || fallback;
}

type Props = {
  open: boolean;
  assetId: string;
  sourceName: string;
  sourcePreviewUrl?: string;
  onClose: () => void;
  onOpenAsset?: (assetId: string) => void;
};

export function SquareImageGenerationDialog(props: Props) {
  const [capabilities, setCapabilities] = useState<ImageGenerationCapabilities | null>(null);
  const [provider, setProvider] = useState<"adobe_firefly" | "gemini">("adobe_firefly");
  const [size, setSize] = useState<1024 | 2048>(1024);
  const [prompt, setPrompt] = useState("");
  const [generation, setGeneration] = useState<ImageGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef("");
  const submittingRef = useRef(false);
  const storageKey = useMemo(() => "cam:image-generation:" + props.assetId, [props.assetId]);

  useEffect(() => {
    if (!props.open) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    requestId.current = crypto.randomUUID();
    const remembered = sessionStorage.getItem(storageKey);
    void Promise.all([
      fetch("/api/v1/image-generations/capabilities", { signal: controller.signal }).then(async response => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw Error(messageFrom(payload, "Unable to load image generation providers."));
        return payload as ImageGenerationCapabilities;
      }),
      remembered
        ? fetch("/api/v1/image-generations/" + encodeURIComponent(remembered), { signal: controller.signal }).then(async response => {
            if (response.status === 404) return null;
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw Error(messageFrom(payload, "Unable to restore image generation."));
            return payload as ImageGeneration;
          })
        : Promise.resolve(null),
    ]).then(([nextCapabilities, rememberedGeneration]) => {
      if (controller.signal.aborted) return;
      setCapabilities(nextCapabilities);
      const available = nextCapabilities.providers.find(item => item.available);
      if (available) setProvider(available.id);
      if (rememberedGeneration) setGeneration(rememberedGeneration);
      else if (remembered) sessionStorage.removeItem(storageKey);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load image generator.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [props.open, props.assetId, storageKey]);

  useEffect(() => {
    if (!props.open || !generation || TERMINAL.has(generation.status)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetch("/api/v1/image-generations/" + encodeURIComponent(generation.id), { signal: controller.signal })
        .then(async response => {
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw Error(messageFrom(payload, "Unable to refresh generation status."));
          if (!controller.signal.aborted) setGeneration(payload as ImageGeneration);
        })
        .catch(reason => {
          if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to refresh generation status.");
        });
    }, POLL_MS);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [props.open, generation]);

  if (!props.open) return null;
  const selected = capabilities?.providers.find(item => item.id === provider);
  const canSubmit = Boolean(capabilities?.enabled && selected?.available && !submitting);
  const active = generation && !TERMINAL.has(generation.status);

  async function submit() {
    if (!canSubmit || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true); setError("");
    try {
      const response = await fetch("/api/v1/image-generations/square", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_asset_id: props.assetId,
          provider,
          target_size: size,
          prompt: prompt.trim() || null,
          client_request_id: requestId.current,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(messageFrom(payload, "Image generation could not be started."));
      const next = payload as ImageGeneration;
      sessionStorage.setItem(storageKey, next.id);
      setGeneration(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Image generation could not be started.");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (!generation || cancelling) return;
    setCancelling(true); setError("");
    try {
      const response = await fetch("/api/v1/image-generations/" + encodeURIComponent(generation.id) + "/cancel", { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(messageFrom(payload, "Generation could not be cancelled."));
      setGeneration(payload as ImageGeneration);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Generation could not be cancelled.");
    } finally {
      setCancelling(false);
    }
  }

  function startAnother() {
    sessionStorage.removeItem(storageKey);
    requestId.current = crypto.randomUUID();
    setGeneration(null); setError("");
  }

  return <div className="image-generation-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && props.onClose()}>
    <section className="image-generation-dialog" role="dialog" aria-modal="true" aria-labelledby="image-generation-title">
      <header><div><small>IMAGE GENERATOR</small><h2 id="image-generation-title">Generate square 1:1</h2></div><button type="button" onClick={props.onClose} aria-label="Close image generator">x</button></header>
      <div className="image-generation-body">
        <div className="image-generation-source">
          {props.sourcePreviewUrl ? <img src={props.sourcePreviewUrl} alt={"Source preview of " + props.sourceName} /> : <span aria-hidden="true">IMG</span>}
          <div><small>SOURCE</small><b title={props.sourceName}>{props.sourceName}</b></div>
        </div>
        {loading && <p role="status">Loading providers...</p>}
        {!loading && !generation && <>
          <fieldset><legend>Provider</legend>{capabilities?.providers.map(item => <label key={item.id} className={!item.available ? "unavailable" : ""}>
            <input type="radio" name="image-provider" value={item.id} checked={provider === item.id} disabled={!item.available} onChange={() => setProvider(item.id)} />
            <span><b>{item.name}</b><small>{item.id === "adobe_firefly" ? "Strict preservation - preserves the source canvas and expands surrounding content." : "Semantic expansion - AI may reinterpret details while preserving the source as faithfully as possible."}</small>{!item.available && <em>Unavailable</em>}</span>
          </label>)}</fieldset>
          <fieldset><legend>Output size</legend><div className="image-generation-sizes">{([1024, 2048] as const).map(value => <label key={value}><input type="radio" name="image-size" checked={size === value} onChange={() => setSize(value)} /><span>{value} x {value}</span></label>)}</div></fieldset>
          <label className="image-generation-prompt">Optional prompt<textarea maxLength={2000} value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="Example: Extend the beach naturally." /></label>
        </>}
        {generation && <div className={"image-generation-progress status-" + generation.status}>
          <div className="image-generation-status"><span aria-hidden="true" /><div><small>STATUS</small><b>{generationStatusLabel(generation.status)}</b><p>{generation.status === "completed" ? "Your generated image is stored in Managed Storage." : generation.status === "failed" ? generation.error?.message || "Generation failed." : generation.status === "cancelled" ? "This generation was cancelled." : "This durable job continues even if you close this dialog."}</p></div></div>
          {generation.status === "completed" && <div className="image-generation-result">
            <img src={"/api/v1/image-generations/" + encodeURIComponent(generation.id) + "/image"} alt="Generated square result" />
            <dl><div><dt>Provider</dt><dd>{generation.model === "local-square-normalize" ? "Local square normalization" : generation.provider === "adobe_firefly" ? "Adobe Firefly" : "Gemini"}</dd></div><div><dt>Resolution</dt><dd>{generation.target_width} x {generation.target_height}</dd></div></dl>
          </div>}
        </div>}
        {error && <p className="image-generation-error" role="alert">{error}</p>}
      </div>
      <footer>
        <button type="button" className="secondary" onClick={props.onClose}>Close</button>
        {!generation && <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>{submitting ? "Starting..." : "Generate"}</button>}
        {active && <button type="button" className="danger" disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "Cancelling..." : "Cancel generation"}</button>}
        {generation?.status === "completed" && <>
          <a className="button secondary" href={"/api/v1/image-generations/" + encodeURIComponent(generation.id) + "/image"} download>Download</a>
          {generation.output_asset_id && props.onOpenAsset && <button type="button" className="primary" onClick={() => props.onOpenAsset!(generation.output_asset_id!)}>Open asset</button>}
          <button type="button" className="secondary" onClick={startAnother}>Generate another</button>
        </>}
        {generation && ["failed", "cancelled"].includes(generation.status) && <button type="button" className="primary" onClick={startAnother}>Try again</button>}
      </footer>
    </section>
  </div>;
}
