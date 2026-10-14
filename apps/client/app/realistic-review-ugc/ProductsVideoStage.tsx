import { useEffect, useMemo, useRef, useState } from "react";
import { listSourcePlans, listColorwayJobs, type ColorwayJob } from "./api";
import type { SourcePlan, SourcePlanPage } from "./types";
import { cancelGeneration, fetchCapabilities, fetchGeneration, videoResultUrl } from "../video-generation/api";
import type { VideoGeneration, VideoGenerationCapabilities } from "../video-generation/types";
import { RrugcStageHeader } from "./RrugcStageHeader";
import "./ProductsVideoStage.css";

const COLORS_MAX = 8;
const PROMPTS = {
  turntable: "Create a photorealistic product video of the exact embroidered Valucap 8869 five-panel cap in the reference image. Slowly rotate the camera around the cap, preserving cap silhouette, embroidery lettering, thread texture, fabric, color and proportions. Real studio lighting, steady movement, no added logos, no visual morphing or flicker.",
  lifestyle: "Create a natural handheld lifestyle product video of the exact hat from the reference. Show a person lifting and putting on the hat, with natural daylight and candid camera movement. Preserve the original embroidery, five-panel silhouette, stitching and all colors with no invented text or graphics.",
  embroidery: "Extreme realistic macro video of fine embroidered threads on the front of the reference hat. Slow side-to-side camera movement revealing raised satin stitch texture, fabric weave and true thread colors. Preserve every letter and detail of the design, no relettering, printing, or CGI effects.",
} as const;

type PromptKey = keyof typeof PROMPTS;
type Stage3Video = VideoGeneration & { colorway_job_ids?: string[] };

type VideoRequest = {
  client_request_id: string;
  colorway_job_ids: string[];
  prompt: string;
  aspect_ratio: string;
  duration_seconds: number;
};

async function videoStageRequest<T>(url: string, init: RequestInit = {}): Promise<T> {
  const result = await fetch(url, {
    credentials: "same-origin",
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  const body = await result.json().catch(() => ({})) as { detail?: string | { message?: string } };
  if (!result.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message || "Video request failed (" + result.status + ")");
  return body as T;
}

export function ProductsVideoStage({ active }: { active: boolean }) {
  const [plans, setPlans] = useState<SourcePlan[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [selectedPlan, setSelectedPlan] = useState("");
  const [colorways, setColorways] = useState<ColorwayJob[]>([]);
  const [selectedImages, setSelectedImages] = useState<string[]>([]);
  const [capabilities, setCapabilities] = useState<VideoGenerationCapabilities | null>(null);
  const [ratio, setRatio] = useState("9:16");
  const [duration, setDuration] = useState(10);
  const [promptPreset, setPromptPreset] = useState<PromptKey>("turntable");
  const [prompt, setPrompt] = useState<string>(PROMPTS.turntable);
  const [videos, setVideos] = useState<Stage3Video[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingColors, setLoadingColors] = useState(false);
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [previewVideoId, setPreviewVideoId] = useState<string | null>(null);
  const retryRef = useRef<VideoRequest | null>(null);
  const pendingRef = useRef(false);

  useEffect(() => {
    const timer = window.setTimeout(() => { setPage(1); setSearch(query); }, 350);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void fetchCapabilities(controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setCapabilities(value);
      setRatio(previous => value.aspect_ratios.includes(previous) ? previous : value.aspect_ratios[0] || "");
      setDuration(previous => value.durations.includes(previous) ? previous : value.durations[0] || 0);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason instanceof Error ? reason.message : reason)); });
    return () => controller.abort();
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true);
    void listSourcePlans({
      page, pageSize: 30, query: search, sortBy: "updated", sortDirection: "desc",
      colorwaySources: true, colorwayWorkflow: "products",
    }, controller.signal).then((result: SourcePlanPage) => {
      if (controller.signal.aborted) return;
      setPlans(result.items);
      setTotal(result.total);
      setSelectedPlan(current => result.items.some(plan => plan.id === current) ? current : result.items[0]?.id || "");
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load Stage 2 designs.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, page, search]);

  useEffect(() => {
    if (!active || !selectedPlan) { setColorways([]); return; }
    const controller = new AbortController();
    setLoadingColors(true);
    setSelectedImages([]);
    void listColorwayJobs([selectedPlan], controller.signal).then(jobs => {
      if (!controller.signal.aborted) setColorways(jobs);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load hat images."); })
      .finally(() => { if (!controller.signal.aborted) setLoadingColors(false); });
    return () => controller.abort();
  }, [active, selectedPlan]);

  useEffect(() => {
    if (!active || !selectedPlan) { setVideos([]); return; }
    const controller = new AbortController();
    let timer: number | undefined;
    const refresh = async () => {
      try {
        const next = await videoStageRequest<Stage3Video[]>(
          "/api/v1/realistic-review-ugc/products-stage3/videos?source_plan_id=" + encodeURIComponent(selectedPlan),
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) setVideos(next);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load video jobs.");
      } finally {
        if (!controller.signal.aborted) setLoadingVideos(false);
      }
    };
    setLoadingVideos(true);
    void refresh();
    timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 7000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [active, selectedPlan]);

  const completed = useMemo(() => colorways.filter(
    job => job.status === "completed" && Boolean(job.output_url),
  ), [colorways]);
  const currentPlan = plans.find(item => item.id === selectedPlan);
  const canGenerate = Boolean(
    capabilities?.enabled && capabilities.models.includes("seedance-2.5")
    && capabilities.aspect_ratios.includes(ratio) && capabilities.durations.includes(duration)
    && selectedImages.length && prompt.trim() && prompt.length <= 4000 && !busy,
  );
  const runningCount = videos.filter(video => !["failed", "cancelled", "completed"].includes(video.status)).length;
  const finishedCount = videos.filter(video => video.status === "completed").length;

  function editDraft(update: () => void) {
    retryRef.current = null;
    setNotice("");
    update();
  }
  function toggleImage(id: string) {
    editDraft(() => setSelectedImages(current => current.includes(id)
      ? current.filter(item => item !== id)
      : current.length < Math.min(COLORS_MAX, capabilities?.max_references || COLORS_MAX)
        ? [...current, id] : current));
  }

  async function generate() {
    if (!canGenerate || pendingRef.current) return;
    pendingRef.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const draft = retryRef.current || {
      client_request_id: crypto.randomUUID(),
      colorway_job_ids: [...selectedImages],
      prompt: prompt.trim(),
      aspect_ratio: ratio,
      duration_seconds: duration,
    };
    retryRef.current = draft;
    try {
      const result = await videoStageRequest<Stage3Video>(
        "/api/v1/realistic-review-ugc/products-stage3/videos",
        { method: "POST", body: JSON.stringify(draft) },
      );
      retryRef.current = null;
      setVideos(previous => [result, ...previous.filter(video => video.id !== result.id)]);
      setNotice("Seedance job created. You can leave this tab; generation continues on the server.");
    } catch (reason) {
      setError((reason instanceof Error ? reason.message : "Unable to create video.") + " Retry request uses the same job ID to avoid duplicates.");
    } finally {
      pendingRef.current = false;
      setBusy(false);
    }
  }

  async function cancel(video: Stage3Video) {
    if (!["queued", "preparing"].includes(video.status)) return;
    try {
      const next = await cancelGeneration(video.id);
      setVideos(current => current.map(item => item.id === video.id ? next : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to cancel video."); }
  }

  async function refreshOne(id: string) {
    try {
      const next = await fetchGeneration(id);
      setVideos(current => current.map(item => item.id === next.id ? next : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to refresh video."); }
  }

  return <section className="rrugc-card rrugc-products-video-stage" aria-label="GatorHats Products Stage 3 Seedance">
    <RrugcStageHeader
      kicker="GATORHATS PRODUCTS · STAGE 3"
      title="Stage 2 photos → Seedance 2.5 videos"
      description={<>Select completed hat images, direct the motion, and send them to the existing durable video queue. No Stage 2 image is altered or deleted.</>}
    />
    <div className="rrugc-video-stage-metrics">
      <div><span>Stage 2 designs</span><strong>{total}</strong></div>
      <div><span>Ready photos · selected design</span><strong>{completed.length}</strong></div>
      <div><span>Selected references</span><strong>{selectedImages.length} / {Math.min(COLORS_MAX, capabilities?.max_references || COLORS_MAX)}</strong></div>
      <div><span>Video jobs · active / complete</span><strong>{runningCount} / {finishedCount}</strong></div>
    </div>
    {!capabilities?.enabled && <p className="rrugc-video-stage-notice" role="status">
      {capabilities === null ? "Checking Seedance capability…" : "Seedance 2.5 is currently unavailable for this tenant. Images and drafts can still be reviewed, but generation is locked."}
    </p>}
    {error && <p className="rrugc-video-stage-error" role="alert">{error}</p>}
    {notice && <p className="rrugc-video-stage-notice" role="status">{notice}</p>}
    <div className="rrugc-video-stage-layout">
      <div className="rrugc-video-stage-library">
        <div className="rrugc-video-stage-section-heading">
          <div><strong>1. Stage 2 image library</strong><small>Choose a design, then up to 8 completed photos</small></div>
          <input type="search" aria-label="Search Stage 2 designs" placeholder="Search designs…" value={query} onChange={event => setQuery(event.target.value)} />
        </div>
        <div className="rrugc-video-stage-source-strip" role="list" aria-label="Stage 2 product designs">
          {plans.map(plan => <button type="button" role="listitem" key={plan.id} className={plan.id === selectedPlan ? "is-selected" : ""} onClick={() => { setSelectedPlan(plan.id); retryRef.current = null; }}>
            <img src={plan.source_preview_url} alt="" loading="lazy" />
            <span title={plan.source_name}>{plan.source_name.replace(/^embroidery_/i, "").replace(/\.[^.]+$/, "").replaceAll("_", " ")}</span>
          </button>)}
          {!loading && plans.length === 0 && <p>No Stage 2 designs found. Generate or select designs in Stage 2 first.</p>}
        </div>
        <div className="rrugc-video-stage-paging">
          <small>{loading ? "Loading designs…" : total + " designs · page " + page}</small>
          <div><button disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}>Previous</button>
          <button disabled={page * 30 >= total || loading} onClick={() => setPage(p => p + 1)}>Next</button></div>
        </div>
        <div className="rrugc-video-stage-section-heading">
          <div><strong>Hat images {currentPlan ? "· " + currentPlan.source_name : ""}</strong>
            <small>Only completed photos are selectable. Existing versions remain in Stage 2.</small></div>
          <button type="button" disabled={!selectedImages.length} onClick={() => editDraft(() => setSelectedImages([]))}>Clear selection</button>
        </div>
        <div className="rrugc-video-stage-images" aria-busy={loadingColors}>
          {completed.map(job => {
            const selected = selectedImages.includes(job.id);
            return <button key={job.id} type="button" className={selected ? "is-selected" : ""}
              aria-pressed={selected} onClick={() => toggleImage(job.id)}
              title={job.color_name + " · " + (selected ? "Remove reference" : "Use as video reference")}>
              <img src={job.output_url! + "?thumbnail=true&size=512"} alt={job.color_name} loading="lazy" />
              <span>{job.color_name}</span>
              <b aria-hidden="true">{selected ? "✓" : "+"}</b>
            </button>;
          })}
          {!loadingColors && !completed.length && <p className="rrugc-video-stage-empty">No completed hat photos for this design. Finish Stage 2 generation, then return here.</p>}
          {loadingColors && <p className="rrugc-video-stage-empty">Loading completed Stage 2 photos…</p>}
        </div>
      </div>
      <div className="rrugc-video-stage-director">
        <div className="rrugc-video-stage-section-heading"><div><strong>2. Video director</strong><small>Seedance 2.5 · image-to-video</small></div></div>
        <div className="rrugc-video-stage-input-grid">
          <label>Model<input value="Seedance 2.5" readOnly /></label>
          <label>Aspect ratio<select aria-label="Video aspect ratio" value={ratio} onChange={event => editDraft(() => setRatio(event.target.value))}>
            {(capabilities?.aspect_ratios || ["9:16", "16:9", "1:1"]).map(item => <option key={item} value={item}>{item}</option>)}
          </select></label>
          <label>Duration<select aria-label="Video duration" value={duration} onChange={event => editDraft(() => setDuration(Number(event.target.value)))}>
            {(capabilities?.durations || [10, 15, 30]).map(item => <option key={item} value={item}>{item}s</option>)}
          </select></label>
          <label>Motion preset<select aria-label="Motion preset" value={promptPreset} onChange={event => {
            const key = event.target.value as PromptKey;
            editDraft(() => { setPromptPreset(key); setPrompt(PROMPTS[key]); });
          }}>
            <option value="turntable">Studio turntable</option>
            <option value="lifestyle">Handheld lifestyle</option>
            <option value="embroidery">Embroidery macro</option>
          </select></label>
        </div>
        <label className="rrugc-video-stage-prompt"><span>Video prompt <small>{prompt.length}/4000</small></span>
          <textarea value={prompt} maxLength={4000} rows={7} onChange={event => editDraft(() => setPrompt(event.target.value))}
            placeholder="Describe the camera, motion, subject and scene. Preserve exact hat embroidery…" /></label>
        <div className="rrugc-video-stage-advice">Keep the embroidery, cap shape, colors and lettering unchanged. One job may use up to eight Stage 2 reference photos.</div>
        <button type="button" className="rrugc-video-stage-generate" onClick={() => void generate()} disabled={!canGenerate}>
          {busy ? "Submitting video…" : "Generate Seedance video · " + selectedImages.length + " ref"}
        </button>
        <div className="rrugc-video-stage-section-heading"><div><strong>3. Video history</strong><small>Saved jobs for the selected design</small></div></div>
        <div className="rrugc-video-stage-history" aria-busy={loadingVideos}>
          {videos.map(video => <article key={video.id} className="rrugc-video-stage-job">
            <div><strong>Seedance 2.5 · {video.aspect_ratio} · {video.duration_seconds}s</strong>
              <small>{new Date(video.created_at).toLocaleString()}</small></div>
            <span data-status={video.status}>{video.status.replaceAll("_", " ")}</span>
            {video.error?.message && <p>{video.error.message}</p>}
            <div className="rrugc-video-stage-job-actions">
              {video.status === "completed" && <button type="button" onClick={() => setPreviewVideoId(video.id)}>Play video</button>}
              {!["completed", "failed", "cancelled"].includes(video.status) && <button onClick={() => void refreshOne(video.id)}>Refresh</button>}
              {["queued", "preparing"].includes(video.status) && <button onClick={() => void cancel(video)}>Cancel</button>}
            </div>
          </article>)}
          {!loadingVideos && !videos.length && <p className="rrugc-video-stage-empty">No video jobs yet. Select finished photos and create your first Seedance video.</p>}
        </div>
      </div>
    </div>
    {previewVideoId && <div className="rrugc-video-stage-modal" role="presentation" onClick={() => setPreviewVideoId(null)}>
      <div role="dialog" aria-modal="true" aria-label="Generated Seedance video" onClick={event => event.stopPropagation()}>
        <button type="button" className="rrugc-video-stage-modal-close" onClick={() => setPreviewVideoId(null)} aria-label="Close video">×</button>
        <video controls autoPlay playsInline preload="metadata" src={videoResultUrl(previewVideoId)} />
      </div>
    </div>}
  </section>;
}
