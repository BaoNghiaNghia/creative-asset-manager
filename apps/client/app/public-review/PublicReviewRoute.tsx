import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { api, type Annotation, type Asset, type Bootstrap, type Child, type Folder, type PlaybackTicket } from "./api";
import { AnnotationEditor } from "./AnnotationEditor";
import { PublicSourceTree } from "./PublicSourceTree";
import { PublicAssetThumbnail, PublicGridSkeleton } from "./PublicAssetThumbnail";
import { RichAnnotation, emptyDocument, type EditorJson } from "./RichAnnotation";
import { normalizedPoint, pinPosition, type Rect } from "./pinGeometry";
import { createReviewPlaybackTicketCache, createReviewPrewarmQueue, reviewMediaOrigin } from "./reviewVideoPerformance";
import { VisualSearchIcon } from "../components/VisualSearchIcon";
import { getFileType } from "../utils/fileType";
const keys = new Map<string, string>(); const exchanges = new Map<string, Promise<unknown>>();
const connectedReviewMediaOrigins = new Set<string>();
function preconnectReviewMedia(ticket: PlaybackTicket) {
 const origin = reviewMediaOrigin(ticket);
 if (!origin || connectedReviewMediaOrigins.has(origin) || typeof document === "undefined") return;
 connectedReviewMediaOrigins.add(origin);
 for (const rel of ["dns-prefetch", "preconnect"] as const) {
  const link = document.createElement("link");
  link.rel = rel;
  link.href = origin;
  if (rel === "preconnect") link.crossOrigin = "anonymous";
  link.dataset.publicReviewMediaOrigin = origin;
  document.head.appendChild(link);
 }
}
export function publicShareIdFromPath(path: string) { return /^\/share\/([A-Za-z0-9_-]{1,128})\/?$/.exec(path)?.[1] || null; }
export function reviewShareUrl(publicId: string, key: string) { return location.origin + "/share/" + encodeURIComponent(publicId) + "#key=" + encodeURIComponent(key); }
function keyFor(id: string) { const value = keys.get(id) || new URLSearchParams(location.hash.slice(1)).get("key"); if (value) { keys.set(id, value); history.replaceState(null, "", location.pathname); } return value || null; }
function exchange(id: string, key: string) { const cacheKey = id + ":" + key; if (!exchanges.has(cacheKey)) exchanges.set(cacheKey, api.exchange(id, key).finally(() => exchanges.delete(cacheKey))); return exchanges.get(cacheKey)!; }
function imageAsset(asset: Asset) { return getFileType(asset.media_type, undefined, asset.filename) === "image"; }
function videoAsset(asset: Asset) { return getFileType(asset.media_type, undefined, asset.filename) === "video"; }
function mediaAsset(asset: Asset) { return imageAsset(asset) || videoAsset(asset); }
export function reviewAssetKey(asset: Pick<Asset, "asset_id" | "source_asset_id">) { return asset.asset_id + ":" + asset.source_asset_id; }
export function reviewMediaPosition(assets: Asset[], current?: Pick<Asset, "asset_id" | "source_asset_id">) {
 const activeKey = current ? reviewAssetKey(current) : "";
 return { keys: assets.map(reviewAssetKey), currentIndex: current ? assets.findIndex(value => reviewAssetKey(value) === activeKey) : -1 };
}
export function reviewAspectRatio(width: number, height: number) {
 if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return "—";
 const ratio = width / height;
 const common = [{ label: "1:1", value: 1 }, { label: "4:5", value: 4 / 5 }, { label: "3:4", value: 3 / 4 }, { label: "4:3", value: 4 / 3 }, { label: "16:9", value: 16 / 9 }, { label: "9:16", value: 9 / 16 }, { label: "3:2", value: 3 / 2 }, { label: "2:3", value: 2 / 3 }, { label: "21:9", value: 21 / 9 }];
 const closest = common.reduce((best, candidate) => Math.abs(candidate.value - ratio) < Math.abs(best.value - ratio) ? candidate : best);
 if (Math.abs(closest.value - ratio) <= 0.035) return closest.label;
 const gcd = (a: number, b: number): number => b ? gcd(b, a % b) : a;
 const w = Math.round(width), h = Math.round(height), divisor = gcd(w, h) || 1;
 return Math.round(w / divisor) + ":" + Math.round(h / divisor);
}
export function formatReviewDuration(seconds: number | null | undefined) {
 if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
 const total = Math.round(seconds), hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), remainder = total % 60;
 return hours > 0 ? hours + ":" + String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(2, "0") : String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(2, "0");
}
function appendChildren(current: Child[], incoming: Child[]) { const known = new Set(current.map(item => item.kind === "folder" ? "f:" + item.source_id + ":" + item.folder_id : "a:" + item.asset_id + ":" + item.source_asset_id)); return [...current, ...incoming.filter(item => !known.has(item.kind === "folder" ? "f:" + item.source_id + ":" + item.folder_id : "a:" + item.asset_id + ":" + item.source_asset_id))]; }
function editableTarget(target: EventTarget | null) { return target instanceof HTMLElement && (target.isContentEditable || target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.closest(".public-editor") !== null); }
function avatarInitials(name: string) { return name.split(/\s+/).filter(Boolean).slice(0, 2).map(value => value[0]).join("").toUpperCase() || "G"; }
function commentTime(value: string) { const time = new Date(value).getTime(); if (!Number.isFinite(time)) return ""; const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000)); if (minutes < 1) return "Just now"; if (minutes < 60) return minutes + "m"; const hours = Math.floor(minutes / 60); if (hours < 24) return hours + "h"; const days = Math.floor(hours / 24); return days + "d"; }
export async function autoplayReviewVideo(video: HTMLVideoElement, isCurrent: () => boolean) {
 if (!isCurrent()) return false;
 try {
  await video.play();
  if (!isCurrent()) { video.pause(); return false; }
  return true;
 } catch { return false; }
}

export function PublicReviewRoute() {
 const id = publicShareIdFromPath(location.pathname); const shareKey = useRef<string | null>(null); const [shareNotice, setShareNotice] = useState(""); const [boot, setBoot] = useState<Bootstrap>(); const [folders, setFolders] = useState<Folder[]>([]); const [activeFolder, setActiveFolder] = useState<Folder>(); const [activeTrail, setActiveTrail] = useState<Folder[]>([]); const [children, setChildren] = useState<Child[]>([]); const [folderAssets, setFolderAssets] = useState<Asset[]>([]); const [assets, setAssets] = useState<Asset[]>([]); const [current, setCurrent] = useState<Asset>(); const [notes, setNotes] = useState<Annotation[]>([]); const [notesKey, setNotesKey] = useState(""); const [query, setQuery] = useState(""); const [mediaFilter, setMediaFilter] = useState<"all" | "images" | "videos">("all"); const [error, setError] = useState(""); const [editor, setEditor] = useState<{ parent?: string; edit?: Annotation; pin?: { x: number; y: number } }>(); const [pinMode, setPinMode] = useState(false); const [selected, setSelected] = useState<string>(); const mediaRef = useRef<HTMLDivElement>(null); const imageRef = useRef<HTMLImageElement>(null); const videoRef = useRef<HTMLVideoElement>(null); const autoplayAttempted = useRef<HTMLVideoElement | null>(null); const videoTicketRetries = useRef(0); const videoResumeAt = useRef(0); const activeVideoKey = useRef<string | null>(null); activeVideoKey.current = current ? current.asset_id + ":" + current.source_asset_id : null; const [videoSource, setVideoSource] = useState<{ key: string; url: string }>(); const [videoReady, setVideoReady] = useState(false); const [autoplayPending, setAutoplayPending] = useState(false); const [videoPlaying, setVideoPlaying] = useState(false); const [videoBuffering, setVideoBuffering] = useState(false); const [videoMuted, setVideoMuted] = useState(true); const [mediaDetails, setMediaDetails] = useState<{ width: number; height: number; duration: number | null }>(); const [geometry, setGeometry] = useState<{ box: Rect; width: number; height: number }>(); const [nextOffset, setNextOffset] = useState<number | null>(null); const [loadingFolder, setLoadingFolder] = useState(false); const [loadingMore, setLoadingMore] = useState(false); const loadMoreRef = useRef<HTMLDivElement>(null); const searchEpoch = useRef(0); const annotationEpoch = useRef(0);
 const searchTimer = useRef<number | null>(null);
 const playbackTickets = useMemo(() => id ? createReviewPlaybackTicketCache(
  asset => api.playbackTicket(id, asset),
  { onTicket: preconnectReviewMedia },
 ) : null, [id]);
 const videoPrewarm = useMemo(() => id ? createReviewPrewarmQueue(
  asset => api.prewarm(id, asset),
  2,
 ) : null, [id]);
 const closeViewer = () => { activeVideoKey.current = null; videoRef.current?.pause(); setEditor(undefined); setPinMode(false); setSelected(undefined); setNotesKey(""); setCurrent(undefined); };
 const selectMediaAsset = (asset: Asset) => { if (videoAsset(asset)) { videoPrewarm?.enqueue(asset, "high"); void playbackTickets?.prefetch(asset).catch(() => undefined); } setEditor(undefined); setPinMode(false); setSelected(undefined); setNotesKey(""); setCurrent(asset); };
 const updateAssetAnnotationCount = (assetKey: string, count: number) => {
  const update = (items: Asset[]) => items.map(item => reviewAssetKey(item) === assetKey ? { ...item, annotation_count: Math.max(0, count) } : item);
  setAssets(update);
  setFolderAssets(update);
 };
 const refreshGeometry = () => { const box = mediaRef.current?.getBoundingClientRect(), image = imageRef.current; if (box && image?.naturalWidth && image.naturalHeight) setGeometry({ box: { left: box.left, top: box.top, width: box.width, height: box.height }, width: image.naturalWidth, height: image.naturalHeight }); };
 useEffect(() => { if (!id) { setError("This review link is unavailable or has expired."); return; } (async () => { try { const key = keyFor(id); if (key) { shareKey.current = key; await exchange(id, key); keys.delete(id); } const values = await Promise.all([api.bootstrap(id), api.folders(id)]); setBoot(values[0]); setFolders(values[1].items);
        const root = values[1].items[0];
        if (root) {
          setLoadingFolder(true);
          const rootChildren = await api.children(id, root);
          const rootAssets = rootChildren.items.filter((item): item is Asset => item.kind === "asset");
          setChildren(rootChildren.items); setFolderAssets(rootAssets); setAssets(rootAssets); setNextOffset(rootChildren.next_offset);
          setActiveFolder(root); setActiveTrail([root]); setLoadingFolder(false);
        } } catch { setError("This review link is unavailable or has expired."); } })(); }, [id]);
 useEffect(() => {
  const epoch = ++annotationEpoch.current;
  if (!id || !current) { setNotes([]); setNotesKey(""); return; }
  const assetKey = reviewAssetKey(current);
  setNotes([]);
  setNotesKey("");
  api.annotations(id, current).then(value => {
   if (epoch === annotationEpoch.current && activeVideoKey.current === assetKey) {
    setNotes(value.items);
    setNotesKey(assetKey);
    updateAssetAnnotationCount(assetKey, value.items.length);
   }
  }).catch(() => {
   if (epoch === annotationEpoch.current && activeVideoKey.current === assetKey) {
    setNotes([]);
    setNotesKey(assetKey);
   }
  });
  return () => { annotationEpoch.current++; };
 }, [id, current]);
 useEffect(() => { videoTicketRetries.current = 0; videoResumeAt.current = 0; setGeometry(undefined); setMediaDetails(undefined); setVideoSource(undefined); setVideoReady(false); setAutoplayPending(false); setVideoPlaying(false); setVideoBuffering(false); }, [current]);
 useEffect(() => {
  if (!playbackTickets || !current || !videoAsset(current)) return;
  const key = reviewAssetKey(current);
  let active = true;
  playbackTickets.get(current).then(ticket => {
   if (active && activeVideoKey.current === key) setVideoSource({ key, url: ticket.url });
  }).catch(() => {
   if (active && activeVideoKey.current === key) setVideoSource({ key, url: current.preview_url });
  });
  return () => { active = false; };
 }, [current, playbackTickets]);
 useEffect(() => { const video = videoRef.current; return () => { video?.pause(); }; }, [current]);
 useEffect(() => { if (!mediaRef.current) return; const observer = new ResizeObserver(refreshGeometry); observer.observe(mediaRef.current); return () => observer.disconnect(); }, [current]);
 useEffect(() => { if (!current) return; const body = document.body; const previousOverflow = body.style.overflow; const previousPaddingRight = body.style.paddingRight; const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth; body.style.overflow = "hidden"; if (scrollbarWidth > 0) body.style.paddingRight = scrollbarWidth + "px"; return () => { body.style.overflow = previousOverflow; body.style.paddingRight = previousPaddingRight; }; }, [current]);
 useEffect(() => { if (!current) return; const on = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); closeViewer(); return; } if (editableTarget(event.target)) return; const index = reviewMediaPosition(assets, current).currentIndex; if (event.key === "ArrowLeft" && index > 0) { event.preventDefault(); selectMediaAsset(assets[index - 1]); } if (event.key === "ArrowRight" && index < assets.length - 1) { event.preventDefault(); selectMediaAsset(assets[index + 1]); } }; window.addEventListener("keydown", on, true); return () => window.removeEventListener("keydown", on, true); }, [current, assets]);
 useEffect(() => {
  if (!current) return;
  const index = reviewMediaPosition(assets, current).currentIndex;
  if (index < 0) return;
  for (const asset of [current, assets[index + 1], assets[index + 2]]) {
   if (asset && videoAsset(asset)) videoPrewarm?.enqueue(asset, "high");
  }
  for (const asset of [assets[index - 1], current, assets[index + 1]]) {
   if (asset && videoAsset(asset)) void playbackTickets?.prefetch(asset).catch(() => undefined);
  }
 }, [current, assets, playbackTickets, videoPrewarm]);
 useEffect(() => {
  if (!videoPrewarm || typeof IntersectionObserver === "undefined") return;
  const byKey = new Map(assets.filter(videoAsset).map(asset => [reviewAssetKey(asset), asset] as const));
  const observer = new IntersectionObserver(entries => {
   let ticketBudget = 2;
   for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const key = (entry.target as HTMLElement).dataset.reviewVideoKey;
    const asset = key ? byKey.get(key) : undefined;
    if (asset) {
     videoPrewarm.enqueue(asset);
     if (ticketBudget > 0) {
      ticketBudget -= 1;
      void playbackTickets?.prefetch(asset).catch(() => undefined);
     }
    }
    observer.unobserve(entry.target);
   }
  }, { rootMargin: "240px 0px" });
  document.querySelectorAll<HTMLElement>("[data-review-video-key]").forEach(node => observer.observe(node));
  return () => observer.disconnect();
 }, [assets, mediaFilter, playbackTickets, videoPrewarm]);
 useEffect(() => { if (!id || !activeFolder || nextOffset === null || query || loadingMore) return; const target = loadMoreRef.current; if (!target || typeof IntersectionObserver === "undefined") return; const observer = new IntersectionObserver(entries => { if (!entries.some(entry => entry.isIntersecting)) return; observer.disconnect(); setLoadingMore(true); api.children(id, activeFolder, nextOffset).then(page => { setChildren(previous => appendChildren(previous, page.items)); const added = page.items.filter((item): item is Asset => item.kind === "asset"); setFolderAssets(previous => appendChildren(previous, added) as Asset[]); setAssets(previous => appendChildren(previous, added) as Asset[]); setNextOffset(page.next_offset); }).catch(() => setNextOffset(null)).finally(() => setLoadingMore(false)); }, { rootMargin: "360px 0px" }); observer.observe(target); return () => observer.disconnect(); }, [id, activeFolder, nextOffset, query, loadingMore]);
 const search = (value: string) => {
  setQuery(value);
  if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
  const normalized = value.trim();
  const epoch = ++searchEpoch.current;
  if (!normalized) {
   setAssets(folderAssets);
   searchTimer.current = null;
   return;
  }
  searchTimer.current = window.setTimeout(() => {
   api.search(id!, normalized)
    .then(result => { if (epoch === searchEpoch.current) setAssets(result.items); })
    .catch(() => { if (epoch === searchEpoch.current) setAssets([]); })
    .finally(() => { if (epoch === searchEpoch.current) searchTimer.current = null; });
  }, 250);
 };
 useEffect(() => () => {
  if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
 }, []);
 const warmVideoInteraction = (asset: Asset) => {
  if (!videoAsset(asset)) return;
  videoPrewarm?.enqueue(asset, "high");
  void playbackTickets?.prefetch(asset).catch(() => undefined);
 };
 if (error) return <main className="public-review state">{error}</main>; if (!id || !boot) return <main className="public-review state">Loading review…</main>;
 const open = async (folder: Folder, trail: Folder[] = [folder]) => { setLoadingFolder(true); try { const value = await api.children(id, folder); setChildren(value.items); const next = value.items.filter((item): item is Asset => item.kind === "asset"); setFolderAssets(next); setActiveFolder(folder); setActiveTrail(trail); setNextOffset(value.next_offset); if (!query) setAssets(next); } finally { setLoadingFolder(false); } };
 const previousAsset = () => { if (!current) return; const index = reviewMediaPosition(assets, current).currentIndex; if (index > 0) selectMediaAsset(assets[index - 1]); };
 const nextAsset = () => { if (!current) return; const index = reviewMediaPosition(assets, current).currentIndex; if (index >= 0 && index < assets.length - 1) selectMediaAsset(assets[index + 1]); };
 const select = (note: Annotation) => { setSelected(note.id); document.getElementById("annotation-" + note.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
 const save = async (content: EditorJson) => {
  if (!current) return;
  const asset = current;
  const assetKey = reviewAssetKey(asset);
  const context = editor || {};
  const value = context.edit ? await api.update(id, context.edit.id, content) : await api.create(id, asset, content, { parentAnnotationId: context.parent, anchorX: context.pin?.x, anchorY: context.pin?.y });
  if (activeVideoKey.current !== assetKey) return;
  setNotes(previous => context.edit ? previous.map(note => note.id === value.id ? value : note) : [...previous, value]);
  if (!context.edit) updateAssetAnnotationCount(assetKey, Math.max(asset.annotation_count || 0, notes.length) + 1);
  setEditor(undefined);
  setPinMode(false);
  if (!context.edit) setSelected(value.id);
 };
 const remove = async (note: Annotation) => {
  if (!current || !confirm("Delete note?")) return;
  const assetKey = reviewAssetKey(current);
  await api.remove(id, note.id);
  if (activeVideoKey.current !== assetKey) return;
  setNotes(previous => previous.filter(value => value.id !== note.id));
  updateAssetAnnotationCount(assetKey, Math.max(0, notes.length - 1));
  if (selected === note.id) setSelected(undefined);
 };
 const isActiveVideo = (video: HTMLVideoElement) => videoRef.current === video && activeVideoKey.current === video.dataset.mediaKey;
 const autoplayWhenReady = (video: HTMLVideoElement) => { if (!isActiveVideo(video) || autoplayAttempted.current === video) return; autoplayAttempted.current = video; setAutoplayPending(true); void autoplayReviewVideo(video, () => isActiveVideo(video)).then(played => { if (isActiveVideo(video)) { setAutoplayPending(false); setVideoPlaying(played); } }); };
 const retryVideoTicket = (video: HTMLVideoElement) => { if (!playbackTickets || !current || !videoAsset(current) || !isActiveVideo(video) || videoTicketRetries.current >= 1) return; videoTicketRetries.current += 1; videoResumeAt.current = Number.isFinite(video.currentTime) ? video.currentTime : 0; autoplayAttempted.current = null; const asset = current; const key = reviewAssetKey(asset); void playbackTickets.get(asset, true).then(ticket => { if (activeVideoKey.current === key) { setVideoReady(false); setVideoBuffering(true); setVideoSource({ key, url: ticket.url }); } }).catch(() => { if (activeVideoKey.current === key) setVideoBuffering(false); }); };
 const captureVideoMetadata = (video: HTMLVideoElement) => { if (!isActiveVideo(video) || !video.videoWidth || !video.videoHeight) return; setMediaDetails({ width: video.videoWidth, height: video.videoHeight, duration: Number.isFinite(video.duration) ? video.duration : null }); }; const toggleVideoMuted = (event: MouseEvent<HTMLButtonElement>) => { event.stopPropagation(); const next = !videoMuted; setVideoMuted(next); if (videoRef.current) videoRef.current.muted = next; }; const toggleVideo = () => { const video = videoRef.current; if (!video || !videoReady) return; if (video.paused) { autoplayAttempted.current = video; void video.play().catch(() => { if (isActiveVideo(video)) setVideoPlaying(false); }); } else video.pause(); }; const clickMedia = (event: MouseEvent<HTMLDivElement>) => { if (!current) return; if (videoAsset(current)) { toggleVideo(); return; } if (!pinMode || !geometry || !imageAsset(current)) return; const point = normalizedPoint(geometry.box, geometry.width, geometry.height, event.clientX, event.clientY); if (point) setEditor({ pin: point }); }; const openNote = (asset: Asset) => { selectMediaAsset(asset); setEditor({}); }; const copyReviewLink = async () => { if (!shareKey.current) { setShareNotice("The review link is unavailable in this browser session."); return; } try { await navigator.clipboard.writeText(reviewShareUrl(id, shareKey.current)); setShareNotice("Review link copied."); } catch { setShareNotice("Unable to copy the review link."); } };
 const mediaPosition = reviewMediaPosition(assets, current); const currentIndex = mediaPosition.currentIndex; const currentMediaKey = current ? reviewAssetKey(current) : ""; const activeVideoSource = current && videoAsset(current) ? (videoSource?.key === currentMediaKey ? videoSource.url : playbackTickets?.peek(current)?.url) : undefined; const reviewResolution = mediaDetails ? mediaDetails.width + " × " + mediaDetails.height : "—"; const reviewDuration = current && videoAsset(current) ? formatReviewDuration(mediaDetails?.duration) : "—"; const reviewRatio = mediaDetails ? reviewAspectRatio(mediaDetails.width, mediaDetails.height) : "—"; const composerKey = currentMediaKey + ":" + (editor?.edit?.id || editor?.parent || (editor?.pin ? "pin" : "root")); const visibleNotes = notesKey === currentMediaKey ? notes : []; const notesLoading = Boolean(current && notesKey !== currentMediaKey); const roots = visibleNotes.filter(note => !note.parent_annotation_id); const pins = visibleNotes.filter(note => note.anchor_x !== null && note.anchor_y !== null); const visibleAssets = assets.filter(asset => mediaFilter === "all" || mediaFilter === "images" && imageAsset(asset) || mediaFilter === "videos" && videoAsset(asset));
 return <main className="public-review"><header className="public-explorer-toolbar"><div className="public-search-field"><span aria-hidden="true">⌕</span><input aria-label="Search images and videos" placeholder="Search images & videos" value={query} onChange={event => search(event.target.value)}/><span className="public-search-visual" aria-hidden="true"><VisualSearchIcon /></span></div><div className="public-media-filter" aria-label="Media filter"><button className={mediaFilter === "all" ? "active" : ""} onClick={() => setMediaFilter("all")}>All</button><button className={mediaFilter === "images" ? "active" : ""} onClick={() => setMediaFilter("images")}>Images</button><button className={mediaFilter === "videos" ? "active" : ""} onClick={() => setMediaFilter("videos")}>Videos</button></div></header><div className="public-workspace"><PublicSourceTree shareId={id} roots={folders} active={activeFolder} activeTrail={activeTrail} onOpen={(folder, trail) => void open(folder, trail)}/><section className="public-folder-detail"><div className="public-breadcrumb"><span>Shared drive</span>{activeTrail.map((folder, index) => <span key={folder.source_id + folder.folder_id}> / {index === activeTrail.length - 1 ? <b>{folder.name}</b> : <button onClick={() => void open(folder, activeTrail.slice(0, index + 1))}>{folder.name}</button>}</span>)}</div><div className="public-folder-header"><div><h2>{query ? "Search results" : activeFolder?.name || "Shared folders"}</h2><p>{query ? "Matching files in this shared review." : children.length + " items in this folder."}</p></div><span className="public-read-only">View only</span></div>{shareNotice && <p className="public-share-notice" role="status">{shareNotice}</p>}{loadingFolder ? <PublicGridSkeleton/> : <section className="public-grid">{!query && children.filter((item): item is Folder & { kind: "folder" } => item.kind === "folder").map(folder => <button className={"public-card public-folder-card " + (folder.name.startsWith("Amazon") ? "amazon" : folder.name.startsWith("Etsy") ? "etsy" : "")} key={folder.source_id + folder.folder_id} onClick={() => void open(folder, [...activeTrail, folder])}><span className="public-card-check" aria-hidden="true"/><span className="public-card-thumb public-folder-glyph"><span className="public-folder-icon"/></span><span className="public-card-copy"><b>{folder.name}</b><small>Folder</small><span className="public-status">Discovered</span></span></button>)}{visibleAssets.map(asset => <article className={"public-card" + (mediaAsset(asset) ? " public-media-card" : "")} key={asset.asset_id + asset.source_asset_id} data-review-video-key={videoAsset(asset) ? reviewAssetKey(asset) : undefined} onMouseEnter={() => warmVideoInteraction(asset)} onFocus={() => warmVideoInteraction(asset)}><button type="button" className="public-card-open" onPointerDown={() => warmVideoInteraction(asset)} onClick={() => selectMediaAsset(asset)} aria-label={"Open " + asset.filename}><span className="public-card-check" aria-hidden="true"/><span className="public-card-info" aria-hidden="true">i</span><span className="public-card-thumb"><PublicAssetThumbnail asset={asset}/></span>{videoAsset(asset) && <span className="public-video-card-badge" aria-hidden="true">▶</span>}<span className="public-card-copy"><b>{asset.filename}</b><small>{imageAsset(asset) ? "Image" : videoAsset(asset) ? "Video" : "File"}</small><span className="public-status">Shared</span>{mediaAsset(asset) && <span className="public-card-rating" aria-hidden="true">★★★★★</span>}</span></button>{mediaAsset(asset) && <span className="public-card-actions" aria-label={"Actions for " + asset.filename}><button type="button" className="public-card-comment-action" onClick={() => openNote(asset)} aria-label={(asset.annotation_count || 0) > 0 ? (asset.annotation_count || 0) + " comments on " + asset.filename : "Add note to " + asset.filename} title={(asset.annotation_count || 0) > 0 ? (asset.annotation_count || 0) + " comments" : "Add note"}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.5a7.5 7.5 0 0 1-7.8 7.5 8.4 8.4 0 0 1-3.4-.7L4 20l1.4-4A7.1 7.1 0 0 1 4 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z"/></svg>{(asset.annotation_count || 0) > 0 && <span className="public-card-comment-count" aria-hidden="true">{(asset.annotation_count || 0) > 99 ? "99+" : asset.annotation_count}</span>}</button><button type="button" onClick={() => void copyReviewLink()} aria-label="Copy review link" title="Copy review link"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.8 7.5-4.4M8.2 13.2l7.5 4.4"/></svg></button></span>}</article>)}{!children.length && !visibleAssets.length && <p className="public-empty">No shared items in this folder.</p>}{!query && nextOffset !== null && <><div ref={loadMoreRef} className="public-load-more-sentinel" aria-hidden="true"/>{loadingMore && <span className="public-load-more">Loading more shared items…</span>}</>}</section>}</section></div>{current && <div className="public-review-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) closeViewer(); }}>
  <section className="public-viewer" role="dialog" aria-modal="true" aria-label={"Review " + current.filename} onMouseDown={event => event.stopPropagation()}>
    <button type="button" className="public-viewer-close" onClick={closeViewer} aria-label="Close" title="Close"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
    <div ref={mediaRef} className={"public-media-stage" + (pinMode ? " pin-mode" : "")} onClick={clickMedia}>
      <div className="public-media-position" aria-label={`Media ${currentIndex + 1} of ${assets.length}`} role="status">
        {mediaPosition.keys.map(key => <span key={key} className={key === currentMediaKey ? "active" : ""} aria-hidden="true"/>)}
      </div>
      {boot.allow_comments && imageAsset(current) && <button type="button" className={"public-pin-toggle" + (pinMode ? " active" : "")} aria-label="Add pin" title="Add pin" aria-pressed={pinMode} onClick={event => { event.stopPropagation(); setPinMode(value => !value); setEditor(undefined); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s7-6.1 7-12a7 7 0 1 0-14 0c0 5.9 7 12 7 12Z"/><circle cx="12" cy="9" r="2.4"/></svg></button>}
      {currentIndex > 0 && <button type="button" className="public-media-nav public-media-nav-previous" aria-label="Previous asset" title="Previous asset" onClick={event => { event.stopPropagation(); previousAsset(); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 5-7 7 7 7"/></svg></button>}
      {videoAsset(current) ? <><video ref={videoRef} key={currentMediaKey} data-media-key={currentMediaKey} className="public-video-preview" preload="auto" poster={current.thumbnail_url} src={activeVideoSource} muted={videoMuted} playsInline onLoadedMetadata={event => { captureVideoMetadata(event.currentTarget); if (isActiveVideo(event.currentTarget) && videoResumeAt.current > 0) { const resume = videoResumeAt.current; videoResumeAt.current = 0; event.currentTarget.currentTime = Number.isFinite(event.currentTarget.duration) ? Math.min(resume, Math.max(0, event.currentTarget.duration - 0.05)) : resume; } }} onLoadedData={event => { if (isActiveVideo(event.currentTarget)) setVideoBuffering(false); }} onError={event => retryVideoTicket(event.currentTarget)} onCanPlay={event => { if (isActiveVideo(event.currentTarget)) { setVideoReady(true); setVideoBuffering(false); autoplayWhenReady(event.currentTarget); } }} onWaiting={event => { if (isActiveVideo(event.currentTarget) && videoReady) setVideoBuffering(true); }} onPlaying={event => { if (isActiveVideo(event.currentTarget)) { setAutoplayPending(false); setVideoPlaying(true); setVideoBuffering(false); } }} onPause={event => { if (isActiveVideo(event.currentTarget)) setVideoPlaying(false); }}>Your browser cannot play this video.</video><button type="button" className="public-video-mute" aria-label={videoMuted ? "Bật âm thanh" : "Tắt âm thanh"} title={videoMuted ? "Bật âm thanh" : "Tắt âm thanh"} aria-pressed={videoMuted} onClick={toggleVideoMuted}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4h4l5 4V6L8 10H4Z"/>{videoMuted ? <><path d="m17 9 4 6"/><path d="m21 9-4 6"/></> : <><path d="M16 9.2a4 4 0 0 1 0 5.6"/><path d="M18.5 6.8a7.3 7.3 0 0 1 0 10.4"/></>}</svg></button>{(!videoReady || videoBuffering || autoplayPending) && <span className="public-video-loading" aria-label="Loading video"><i/></span>}{videoReady && !videoPlaying && !videoBuffering && !autoplayPending && <button type="button" className="public-video-play" onClick={event => { event.stopPropagation(); toggleVideo(); }} aria-label="Play video"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 9 6-9 6z"/></svg></button>}</> : <img key={currentMediaKey} ref={imageRef} onLoad={event => { refreshGeometry(); const image = event.currentTarget; if (image.naturalWidth && image.naturalHeight) setMediaDetails({ width: image.naturalWidth, height: image.naturalHeight, duration: null }); }} src={current.preview_url} alt={current.filename} loading="eager" decoding="async"/>}
      {currentIndex >= 0 && currentIndex < assets.length - 1 && <button type="button" className="public-media-nav public-media-nav-next" aria-label="Next asset" title="Next asset" onClick={event => { event.stopPropagation(); nextAsset(); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9.5 5 7 7-7 7"/></svg></button>}
      {imageAsset(current) && geometry && pins.map((note, index) => { const position = pinPosition(geometry.box, geometry.width, geometry.height, note.anchor_x!, note.anchor_y!); return position ? <button key={note.id} className={"public-pin" + (selected === note.id ? " selected" : "")} style={{ left: position.x - geometry.box.left, top: position.y - geometry.box.top }} aria-label={"Open comment " + (index + 1)} onClick={event => { event.stopPropagation(); select(note); }}>{index + 1}</button> : null; })}
      {imageAsset(current) && editor?.pin && geometry && (() => { const position = pinPosition(geometry.box, geometry.width, geometry.height, editor.pin.x, editor.pin.y); return position ? <span className="public-pin pending" style={{ left: position.x - geometry.box.left, top: position.y - geometry.box.top }}>+</span> : null; })()}
    </div>
    <aside className="public-review-sidebar">
      <header className="public-review-header"><div className="public-review-heading"><h2>Review</h2><div className="public-review-metadata" aria-label="Media information"><span><small>Resolution</small><b>{reviewResolution}</b></span><span><small>Duration</small><b>{reviewDuration}</b></span><span><small>Ratio</small><b>{reviewRatio}</b></span></div></div></header>
      <div className="public-review-feed" aria-busy={notesLoading}>{notesLoading ? <div className="public-review-notes-loading" role="status" aria-label="Loading comments"><span/><span/><span/></div> : roots.length ? roots.map(note => <article id={"annotation-" + note.id} className={"public-comment" + (selected === note.id ? " selected" : "")} key={note.id} onClick={() => select(note)}><span className="public-comment-avatar" aria-hidden="true">{avatarInitials(note.author.display_name)}</span><div className="public-comment-body"><b>{note.author.display_name}</b><RichAnnotation document={note.content_json || emptyDocument()}/><footer className="public-comment-footer"><time dateTime={note.created_at}>{commentTime(note.created_at)}</time>{boot.allow_comments && <div className="public-note-actions"><button type="button" aria-label="Reply" title="Reply" onClick={event => { event.stopPropagation(); setEditor({ parent: note.id }); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 8 4 12l5 4v-3h5a5 5 0 0 1 5 5c1-5-1-9-6-9H9V8Z"/></svg></button>{note.can_edit && <button type="button" aria-label="Edit" title="Edit" onClick={event => { event.stopPropagation(); setEditor({ edit: note }); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16.5-.8 4.3 4.3-.8L19 8.5 15.5 5 4 16.5Z"/><path d="m13.8 6.7 3.5 3.5"/></svg></button>}{note.can_delete && <button type="button" className="public-note-delete" aria-label="Delete" title="Delete" onClick={event => { event.stopPropagation(); void remove(note); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5"/></svg></button>}</div>}</footer>{visibleNotes.filter(value => value.parent_annotation_id === note.id).map(reply => <div id={"annotation-" + reply.id} className="public-reply public-comment-reply" key={reply.id} onClick={() => select(reply)}><span className="public-comment-avatar" aria-hidden="true">{avatarInitials(reply.author.display_name)}</span><div className="public-comment-body"><b>{reply.author.display_name}</b><RichAnnotation document={reply.content_json || emptyDocument()}/><time dateTime={reply.created_at}>{commentTime(reply.created_at)}</time></div></div>)}</div></article>) : <p className="public-review-empty">No comments yet</p>}</div>
      <div className="public-review-composer">{boot.allow_comments ? <AnnotationEditor key={composerKey} initial={editor?.edit?.content_json} placeholder="Bình luận..." submitLabel="Đăng" onSubmit={save}/> : <p>Comments are disabled for this review.</p>}</div>
    </aside>
  </section>
</div>}</main>;
}