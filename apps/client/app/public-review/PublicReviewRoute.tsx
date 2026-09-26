import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent } from "react";
import { api, type Annotation, type Asset, type Bootstrap, type Child, type Folder, type PlaybackTicket, type PublicCommentActivity } from "./api";
import { AnnotationEditor } from "./AnnotationEditor";
import { PublicSourceTree } from "./PublicSourceTree";
import { PublicReviewHistory, type PublicActivityTab } from "./PublicReviewHistory";
import { PublicAssetThumbnail, PublicGridSkeleton } from "./PublicAssetThumbnail";
import { RichAnnotation, emptyDocument, type EditorJson } from "./RichAnnotation";
import { normalizedPoint, pinPosition, type Rect } from "./pinGeometry";
import { clearReviewHistory, historyEntryAsset, readReviewHistory, recordReviewHistory, type ReviewHistoryEntry } from "./viewHistory";
import { createReviewPlaybackTicketCache, createReviewPrewarmQueue, reviewMediaOrigin } from "./reviewVideoPerformance";
import { BrandIcon } from "../components/Icons";
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
export function publicReviewLocationFromPath(path: string) {
 const match = /^\/share\/([A-Za-z0-9_-]{1,128})(?:\/folder\/([^/]{1,1024}))?\/?$/.exec(path);
 if (!match) return null;
 try {
  return { publicId: match[1], folderId: match[2] ? decodeURIComponent(match[2]) : null };
 } catch {
  return null;
 }
}
export function publicShareIdFromPath(path: string) { return publicReviewLocationFromPath(path)?.publicId || null; }
export function publicFolderIdFromPath(path: string) { return publicReviewLocationFromPath(path)?.folderId || null; }
export function reviewFolderPath(publicId: string, folderId?: string | null) {
 const base = "/share/" + encodeURIComponent(publicId);
 return folderId ? base + "/folder/" + encodeURIComponent(folderId) : base;
}
export function reviewShareUrl(publicId: string, key: string, folderId?: string | null) { return location.origin + reviewFolderPath(publicId, folderId) + "#key=" + encodeURIComponent(key); }
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
function avatarTone(name: string) { let hash = 2166136261; for (const char of name.trim().toLowerCase()) { hash ^= char.codePointAt(0) || 0; hash = Math.imul(hash, 16777619); } return Math.abs(hash >>> 0) % 8; }
function commentTime(value: string) { const time = new Date(value).getTime(); if (!Number.isFinite(time)) return ""; const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000)); if (minutes < 1) return "Just now"; if (minutes < 60) return minutes + "m"; const hours = Math.floor(minutes / 60); if (hours < 24) return hours + "h"; const days = Math.floor(hours / 24); return days + "d"; }
function commentExcerpt(note: Annotation, limit = 96) { const text = (note.plain_text || "").replace(/\s+/g, " ").trim(); if (!text) return "Comment"; return text.length > limit ? text.slice(0, limit - 1).trimEnd() + "…" : text; }
const PUBLIC_SEARCH_HISTORY_PREFIX = "creative-asset-manager:public-review-search-history:v1:";
const PUBLIC_SEARCH_HISTORY_LIMIT = 10;
function normalizeSearchTerm(value: string) { return value.trim().replace(/\s+/g, " "); }
function publicSearchHistoryKey(id: string | null) { return id ? PUBLIC_SEARCH_HISTORY_PREFIX + id : null; }
function loadPublicSearchHistory(id: string | null) {
 const key = publicSearchHistoryKey(id); if (!key || typeof window === "undefined") return [] as string[];
 try { const parsed: unknown = JSON.parse(window.localStorage.getItem(key) || "[]"); if (!Array.isArray(parsed)) return []; const seen = new Set<string>(); return parsed.flatMap(item => { const text = typeof item === "string" ? normalizeSearchTerm(item) : ""; const normalized = text.toLocaleLowerCase(); if (!text || seen.has(normalized)) return []; seen.add(normalized); return [text]; }).slice(0, PUBLIC_SEARCH_HISTORY_LIMIT); } catch { return []; }
}
function savePublicSearchHistory(id: string | null, entries: string[]) { const key = publicSearchHistoryKey(id); if (!key || typeof window === "undefined") return; try { window.localStorage.setItem(key, JSON.stringify(entries.slice(0, PUBLIC_SEARCH_HISTORY_LIMIT))); } catch { /* Search history is optional. */ } }
function addPublicSearchHistory(entries: string[], value: string) { const text = normalizeSearchTerm(value); if (!text) return entries; const key = text.toLocaleLowerCase(); return [text, ...entries.filter(item => item.toLocaleLowerCase() !== key)].slice(0, PUBLIC_SEARCH_HISTORY_LIMIT); }
function publicSearchSuggestions(items: Asset[], value: string) { const needle = normalizeSearchTerm(value).toLocaleLowerCase(); if (needle.length < 2) return [] as string[]; const seen = new Set<string>(); return items.flatMap(item => { const text = normalizeSearchTerm(item.filename || ""); const key = text.toLocaleLowerCase(); if (!text || !key.includes(needle) || seen.has(key)) return []; seen.add(key); return [text]; }).slice(0, 10); }
export async function autoplayReviewVideo(video: HTMLVideoElement, isCurrent: () => boolean) {
 if (!isCurrent()) return false;
 try {
  await video.play();
  if (!isCurrent()) { video.pause(); return false; }
  return true;
 } catch { return false; }
}

export function PublicReviewRoute() {
 const id = publicShareIdFromPath(location.pathname); const shareKey = useRef<string | null>(null); const [shareNotice, setShareNotice] = useState(""); const [boot, setBoot] = useState<Bootstrap>(); const [folders, setFolders] = useState<Folder[]>([]); const [activeFolder, setActiveFolder] = useState<Folder>(); const [activeTrail, setActiveTrail] = useState<Folder[]>([]); const [children, setChildren] = useState<Child[]>([]); const [folderAssets, setFolderAssets] = useState<Asset[]>([]); const [assets, setAssets] = useState<Asset[]>([]); const [viewerAssets, setViewerAssets] = useState<Asset[]>([]); const [current, setCurrent] = useState<Asset>(); const [viewHistory, setViewHistory] = useState<ReviewHistoryEntry[]>([]); const [activityTab, setActivityTab] = useState<PublicActivityTab | null>(null); const [commentActivity, setCommentActivity] = useState<PublicCommentActivity[]>([]); const [commentsLoading, setCommentsLoading] = useState(false); const [pendingAnnotationId, setPendingAnnotationId] = useState<string>(); const [notes, setNotes] = useState<Annotation[]>([]); const [notesKey, setNotesKey] = useState(""); const [query, setQuery] = useState(""); const [searchSuggestions, setSearchSuggestions] = useState<string[]>([]); const [suggestionIndex, setSuggestionIndex] = useState(-1); const [suggestionsDismissed, setSuggestionsDismissed] = useState(false); const [searchHistory, setSearchHistory] = useState<string[]>(() => loadPublicSearchHistory(id)); const [searchHistoryOpen, setSearchHistoryOpen] = useState(false); const [mediaFilter, setMediaFilter] = useState<"all" | "images" | "videos">("all"); const [error, setError] = useState(""); const [editor, setEditor] = useState<{ parent?: string; edit?: Annotation; pin?: { x: number; y: number } }>(); const [pinMode, setPinMode] = useState(false); const [selected, setSelected] = useState<string>(); const composerRef = useRef<HTMLDivElement>(null); const searchFieldRef = useRef<HTMLDivElement>(null); const mediaRef = useRef<HTMLDivElement>(null); const imageRef = useRef<HTMLImageElement>(null); const videoRef = useRef<HTMLVideoElement>(null); const autoplayAttempted = useRef<HTMLVideoElement | null>(null); const videoTicketRetries = useRef(0); const videoResumeAt = useRef(0); const activeVideoKey = useRef<string | null>(null); activeVideoKey.current = current ? current.asset_id + ":" + current.source_asset_id : null; const [videoSource, setVideoSource] = useState<{ key: string; url: string }>(); const [videoReady, setVideoReady] = useState(false); const [autoplayPending, setAutoplayPending] = useState(false); const [videoPlaying, setVideoPlaying] = useState(false); const [videoBuffering, setVideoBuffering] = useState(false); const [videoMuted, setVideoMuted] = useState(true); const [mediaDetails, setMediaDetails] = useState<{ width: number; height: number; duration: number | null }>(); const [geometry, setGeometry] = useState<{ box: Rect; width: number; height: number }>(); const [nextOffset, setNextOffset] = useState<number | null>(null); const [loadingFolder, setLoadingFolder] = useState(false); const [loadingMore, setLoadingMore] = useState(false); const loadMoreRef = useRef<HTMLDivElement>(null); const searchEpoch = useRef(0); const annotationEpoch = useRef(0); const folderNavigationEpoch = useRef(0);
 const searchTimer = useRef<number | null>(null);
 const playbackTickets = useMemo(() => id ? createReviewPlaybackTicketCache(
  asset => api.playbackTicket(id, asset),
  { onTicket: preconnectReviewMedia },
 ) : null, [id]);
 const videoPrewarm = useMemo(() => id ? createReviewPrewarmQueue(
  asset => api.prewarm(id, asset),
  2,
 ) : null, [id]);
 const closeViewer = () => { activeVideoKey.current = null; videoRef.current?.pause(); setEditor(undefined); setPinMode(false); setSelected(undefined); setNotesKey(""); setCurrent(undefined); setViewerAssets([]); };
 const selectMediaAsset = (asset: Asset, sourceAssets: Asset[] = assets, folderId: string | null | undefined = activeFolder?.folder_id) => { const source = sourceAssets.some(value => reviewAssetKey(value) === reviewAssetKey(asset)) ? sourceAssets : [asset, ...sourceAssets]; setViewerAssets(source); if (id) setViewHistory(recordReviewHistory(id, asset, folderId)); if (videoAsset(asset)) { videoPrewarm?.enqueue(asset, "high"); void playbackTickets?.prefetch(asset).catch(() => undefined); } setEditor(undefined); setPinMode(false); setSelected(undefined); setNotesKey(""); setCurrent(asset); };
 const updateAssetAnnotationCount = (assetKey: string, count: number) => {
  const update = (items: Asset[]) => items.map(item => reviewAssetKey(item) === assetKey ? { ...item, annotation_count: Math.max(0, count) } : item);
  setAssets(update);
  setFolderAssets(update);
 };
 const refreshGeometry = () => { const box = mediaRef.current?.getBoundingClientRect(), image = imageRef.current; if (box && image?.naturalWidth && image.naturalHeight) setGeometry({ box: { left: box.left, top: box.top, width: box.width, height: box.height }, width: image.naturalWidth, height: image.naturalHeight }); };
 const displayFolder = async (folder: Folder, trail: Folder[]) => {
  if (!id) return;
  const epoch = ++folderNavigationEpoch.current;
  setLoadingFolder(true);
  try {
   const page = await api.children(id, folder);
   if (epoch !== folderNavigationEpoch.current) return;
   const next = page.items.filter((item): item is Asset => item.kind === "asset");
   setChildren(page.items); setFolderAssets(next); setActiveFolder(folder); setActiveTrail(trail); setNextOffset(page.next_offset);
   if (!query) setAssets(next);
  } finally {
   if (epoch === folderNavigationEpoch.current) setLoadingFolder(false);
  }
 };
 useEffect(() => { if (!id) { setViewHistory([]); return; } setViewHistory(readReviewHistory(id)); }, [id]);
 useEffect(() => { setSearchHistory(loadPublicSearchHistory(id)); }, [id]);
 useEffect(() => { if (!activityTab || current) return; const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setActivityTab(null); } }; window.addEventListener("keydown", onKeyDown, true); return () => window.removeEventListener("keydown", onKeyDown, true); }, [activityTab, current]);
 useEffect(() => { if (!id) { setError("This review link is unavailable or has expired."); return; } (async () => { try {
        const key = keyFor(id);
        if (key) { shareKey.current = key; await exchange(id, key); keys.delete(id); }
        const values = await Promise.all([api.bootstrap(id), api.folders(id)]);
        setBoot(values[0]);
        setFolders(values[1].items);
        const root = values[1].items[0];
        if (!root) return;
        let folder = root;
        let trail = [root];
        const requestedFolderId = publicFolderIdFromPath(location.pathname);
        if (requestedFolderId) {
          try {
            const resolved = await api.folder(id, requestedFolderId);
            folder = resolved.folder;
            trail = resolved.trail;
          } catch {
            setShareNotice("This shared folder is no longer available.");
            history.replaceState(null, "", reviewFolderPath(id));
          }
        }
        await displayFolder(folder, trail);
      } catch { setError("This review link is unavailable or has expired."); } })(); }, [id]);
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
    if (pendingAnnotationId && value.items.some(note => note.id === pendingAnnotationId)) {
      setSelected(pendingAnnotationId);
      const target = pendingAnnotationId;
      setPendingAnnotationId(undefined);
      window.setTimeout(() => document.getElementById("annotation-" + target)?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }), 0);
    }
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
 useEffect(() => { if (!current) return; const on = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); closeViewer(); return; } if (editableTarget(event.target)) return; const index = reviewMediaPosition(viewerAssets, current).currentIndex; if (event.key === "ArrowLeft" && index > 0) { event.preventDefault(); selectMediaAsset(viewerAssets[index - 1], viewerAssets); } if (event.key === "ArrowRight" && index < viewerAssets.length - 1) { event.preventDefault(); selectMediaAsset(viewerAssets[index + 1], viewerAssets); } }; window.addEventListener("keydown", on, true); return () => window.removeEventListener("keydown", on, true); }, [current, viewerAssets]);
 useEffect(() => {
  if (!current) return;
  const index = reviewMediaPosition(viewerAssets, current).currentIndex;
  if (index < 0) return;
  for (const asset of [current, viewerAssets[index + 1], viewerAssets[index + 2]]) {
   if (asset && videoAsset(asset)) videoPrewarm?.enqueue(asset, "high");
  }
  for (const asset of [viewerAssets[index - 1], current, viewerAssets[index + 1]]) {
   if (asset && videoAsset(asset)) void playbackTickets?.prefetch(asset).catch(() => undefined);
  }
 }, [current, viewerAssets, playbackTickets, videoPrewarm]);
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
  setSuggestionIndex(-1);
  if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
  const normalized = normalizeSearchTerm(value);
  const epoch = ++searchEpoch.current;
  if (normalized.length < 2) setSearchSuggestions([]);
  if (!normalized) {
   setAssets(folderAssets);
   setSearchSuggestions([]);
   searchTimer.current = null;
   return;
  }
  searchTimer.current = window.setTimeout(() => {
   api.search(id!, normalized)
    .then(result => {
     if (epoch !== searchEpoch.current) return;
     setAssets(result.items);
     setSearchSuggestions(publicSearchSuggestions(result.items, normalized));
    })
    .catch(() => {
     if (epoch !== searchEpoch.current) return;
     setAssets([]);
     setSearchSuggestions([]);
    })
    .finally(() => { if (epoch === searchEpoch.current) searchTimer.current = null; });
  }, 250);
 };
 const commitSearch = (value: string) => {
  const normalized = normalizeSearchTerm(value);
  if (!normalized) return;
  if (normalized !== query) search(normalized);
  setSearchHistory(currentHistory => {
   const next = addPublicSearchHistory(currentHistory, normalized);
   savePublicSearchHistory(id, next);
   return next;
  });
  setSearchHistoryOpen(false);
  setSuggestionsDismissed(true);
  setSuggestionIndex(-1);
 };
 const removeSearchHistory = (value: string) => {
  setSearchHistory(currentHistory => {
   const next = currentHistory.filter(item => item !== value);
   savePublicSearchHistory(id, next);
   return next;
  });
 };
 const clearSearchHistory = () => { setSearchHistory([]); savePublicSearchHistory(id, []); };
 const showSearchSuggestions = !suggestionsDismissed && query.trim().length >= 2 && searchSuggestions.length > 0;
 const showSearchHistory = searchHistoryOpen && !showSearchSuggestions && !query.trim() && searchHistory.length > 0;
 const handleSearchKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
  if (event.key === "Escape") {
   event.preventDefault();
   search("");
   setSearchHistoryOpen(false);
   setSuggestionsDismissed(true);
   setSuggestionIndex(-1);
   return;
  }
  if (!showSearchSuggestions) {
   if (event.key === "Enter") { event.preventDefault(); commitSearch(query); }
   return;
  }
  if (event.key === "ArrowDown") { event.preventDefault(); setSuggestionIndex(currentIndex => (currentIndex + 1) % searchSuggestions.length); return; }
  if (event.key === "ArrowUp") { event.preventDefault(); setSuggestionIndex(currentIndex => (currentIndex - 1 + searchSuggestions.length) % searchSuggestions.length); return; }
  if (event.key === "Enter") {
   event.preventDefault();
   if (suggestionIndex >= 0) commitSearch(searchSuggestions[suggestionIndex]);
   else commitSearch(query);
  }
 };
 useEffect(() => () => {
  if (searchTimer.current !== null) window.clearTimeout(searchTimer.current);
 }, []);
 useEffect(() => {
  if (!id || !folders.length) return;
  const onPopState = () => {
   const route = publicReviewLocationFromPath(location.pathname);
   if (!route || route.publicId !== id) return;
   const root = folders[0];
   if (!route.folderId) {
    void displayFolder(root, [root]);
    return;
   }
   api.folder(id, route.folderId)
    .then(value => displayFolder(value.folder, value.trail))
    .catch(() => {
     setShareNotice("This shared folder is no longer available.");
     history.replaceState(null, "", reviewFolderPath(id));
     void displayFolder(root, [root]);
    });
  };
  window.addEventListener("popstate", onPopState);
  return () => window.removeEventListener("popstate", onPopState);
 }, [id, folders, query]);
 const warmVideoInteraction = (asset: Asset) => {
  if (!videoAsset(asset)) return;
  videoPrewarm?.enqueue(asset, "high");
  void playbackTickets?.prefetch(asset).catch(() => undefined);
 };
 if (error) return <main className="public-review state">{error}</main>; if (!id || !boot) return <main className="public-review state">Loading review…</main>;
 const open = async (folder: Folder, trail: Folder[] = [folder]) => {
  await displayFolder(folder, trail);
  const nextPath = reviewFolderPath(id, folder.folder_id);
  if (location.pathname !== nextPath) history.pushState(null, "", nextPath);
 };
 const refreshCommentActivity = async () => { setCommentsLoading(true); try { const value = await api.comments(id); setCommentActivity(value.items); } catch { setCommentActivity([]); } finally { setCommentsLoading(false); } };
 const selectActivityTab = (tab: PublicActivityTab) => { if (activityTab === tab) { setActivityTab(null); return; } setActivityTab(tab); if (tab === "history") setViewHistory(readReviewHistory(id)); else void refreshCommentActivity(); };
 const openHistoryEntry = (entry: ReviewHistoryEntry) => { const refreshed = readReviewHistory(id); const historyAssets = refreshed.map(historyEntryAsset); setActivityTab(null); selectMediaAsset(historyEntryAsset(entry), historyAssets, entry.folder_id); };
 const openCommentEntry = (entry: PublicCommentActivity) => { setActivityTab(null); setPendingAnnotationId(entry.annotation.id); selectMediaAsset(entry.asset, [entry.asset]); };
 const clearHistoryItems = () => { if (!confirm("Clear viewing history on this device?")) return; clearReviewHistory(id); setViewHistory([]); };
 const previousAsset = () => { if (!current) return; const index = reviewMediaPosition(viewerAssets, current).currentIndex; if (index > 0) selectMediaAsset(viewerAssets[index - 1], viewerAssets); };
 const nextAsset = () => { if (!current) return; const index = reviewMediaPosition(viewerAssets, current).currentIndex; if (index >= 0 && index < viewerAssets.length - 1) selectMediaAsset(viewerAssets[index + 1], viewerAssets); };
 const select = (note: Annotation) => { setSelected(note.id); document.getElementById("annotation-" + note.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
 const beginReply = (note: Annotation) => { setSelected(note.id); setPinMode(false); setEditor({ parent: note.id }); window.setTimeout(() => { composerRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); composerRef.current?.querySelector<HTMLElement>(".public-tiptap")?.focus(); }, 0); };
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
  void refreshCommentActivity();
  if (!context.edit) setSelected(value.id);
 };
 const remove = async (note: Annotation) => {
  if (!current || !confirm("Delete note?")) return;
  const assetKey = reviewAssetKey(current);
  await api.remove(id, note.id);
  if (activeVideoKey.current !== assetKey) return;
  setNotes(previous => previous.filter(value => value.id !== note.id));
  updateAssetAnnotationCount(assetKey, Math.max(0, notes.length - 1));
  void refreshCommentActivity();
  if (selected === note.id) setSelected(undefined);
 };
 const isActiveVideo = (video: HTMLVideoElement) => videoRef.current === video && activeVideoKey.current === video.dataset.mediaKey;
 const autoplayWhenReady = (video: HTMLVideoElement) => { if (!isActiveVideo(video) || autoplayAttempted.current === video) return; autoplayAttempted.current = video; setAutoplayPending(true); void autoplayReviewVideo(video, () => isActiveVideo(video)).then(played => { if (isActiveVideo(video)) { setAutoplayPending(false); setVideoPlaying(played); } }); };
 const retryVideoTicket = (video: HTMLVideoElement) => { if (!playbackTickets || !current || !videoAsset(current) || !isActiveVideo(video) || videoTicketRetries.current >= 1) return; videoTicketRetries.current += 1; videoResumeAt.current = Number.isFinite(video.currentTime) ? video.currentTime : 0; autoplayAttempted.current = null; const asset = current; const key = reviewAssetKey(asset); void playbackTickets.get(asset, true).then(ticket => { if (activeVideoKey.current === key) { setVideoReady(false); setVideoBuffering(true); setVideoSource({ key, url: ticket.url }); } }).catch(() => { if (activeVideoKey.current === key) setVideoBuffering(false); }); };
 const captureVideoMetadata = (video: HTMLVideoElement) => { if (!isActiveVideo(video) || !video.videoWidth || !video.videoHeight) return; setMediaDetails({ width: video.videoWidth, height: video.videoHeight, duration: Number.isFinite(video.duration) ? video.duration : null }); }; const toggleVideoMuted = (event: MouseEvent<HTMLButtonElement>) => { event.stopPropagation(); const next = !videoMuted; setVideoMuted(next); if (videoRef.current) videoRef.current.muted = next; }; const toggleVideo = () => { const video = videoRef.current; if (!video || !videoReady) return; if (video.paused) { autoplayAttempted.current = video; void video.play().catch(() => { if (isActiveVideo(video)) setVideoPlaying(false); }); } else video.pause(); }; const clickMedia = (event: MouseEvent<HTMLDivElement>) => { if (!current) return; if (videoAsset(current)) { toggleVideo(); return; } if (!pinMode || !geometry || !imageAsset(current)) return; const point = normalizedPoint(geometry.box, geometry.width, geometry.height, event.clientX, event.clientY); if (point) setEditor({ pin: point }); }; const openNote = (asset: Asset) => { selectMediaAsset(asset); setEditor({}); }; const copyReviewLink = async () => { if (!shareKey.current) { setShareNotice("The review link is unavailable in this browser session."); return; } try { await navigator.clipboard.writeText(reviewShareUrl(id, shareKey.current, activeFolder?.folder_id)); setShareNotice("Review link copied."); } catch { setShareNotice("Unable to copy the review link."); } };
 const mediaPosition = reviewMediaPosition(viewerAssets, current); const currentIndex = mediaPosition.currentIndex; const currentMediaKey = current ? reviewAssetKey(current) : ""; const activeVideoSource = current && videoAsset(current) ? (videoSource?.key === currentMediaKey ? videoSource.url : playbackTickets?.peek(current)?.url) : undefined; const reviewResolution = mediaDetails ? mediaDetails.width + " × " + mediaDetails.height : "—"; const reviewDuration = current && videoAsset(current) ? formatReviewDuration(mediaDetails?.duration) : "—"; const reviewRatio = mediaDetails ? reviewAspectRatio(mediaDetails.width, mediaDetails.height) : "—"; const composerKey = currentMediaKey + ":" + (editor?.edit?.id || editor?.parent || (editor?.pin ? "pin" : "root")); const visibleNotes = notesKey === currentMediaKey ? notes : []; const notesLoading = Boolean(current && notesKey !== currentMediaKey); const roots = visibleNotes.filter(note => !note.parent_annotation_id); const replyTarget = editor?.parent ? roots.find(note => note.id === editor.parent) : undefined; const pins = visibleNotes.filter(note => note.anchor_x !== null && note.anchor_y !== null); const visibleAssets = assets.filter(asset => mediaFilter === "all" || mediaFilter === "images" && imageAsset(asset) || mediaFilter === "videos" && videoAsset(asset));
 return <main className="public-review"><header className="public-explorer-toolbar">
  <div className="public-share-brand brand"><b aria-hidden="true"><BrandIcon /></b><span><strong>Creative assets</strong><small>liveview content</small></span></div>
  <div ref={searchFieldRef} className="public-search-field" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) { setSearchHistoryOpen(false); setSuggestionsDismissed(true); setSuggestionIndex(-1); } }}>
    <span aria-hidden="true">⌕</span>
    <input
      aria-label="Search images and videos"
      placeholder="Search images & videos"
      value={query}
      onFocus={() => { setSearchHistoryOpen(!query.trim()); setSuggestionsDismissed(false); }}
      onChange={event => { setSearchHistoryOpen(false); setSuggestionsDismissed(false); search(event.target.value); }}
      onKeyDown={handleSearchKeyDown}
      aria-autocomplete="list"
      aria-expanded={showSearchSuggestions || showSearchHistory}
      aria-controls={showSearchSuggestions ? "public-search-suggestions" : showSearchHistory ? "public-search-history" : undefined}
      aria-activedescendant={showSearchSuggestions && suggestionIndex >= 0 ? "public-search-suggestion-" + suggestionIndex : undefined}
    />
    {query && <button type="button" className="public-search-clear" aria-label="Clear search" title="Clear search" onClick={() => { search(""); setSearchHistoryOpen(true); setSuggestionsDismissed(true); setSuggestionIndex(-1); }}>×</button>}
    <span className="public-search-visual" aria-hidden="true"><VisualSearchIcon /></span>
    {showSearchHistory && <div id="public-search-history" className="search-history" role="listbox" aria-label="Recent searches">
      <div className="search-history-header"><strong>Recent searches</strong><button type="button" onMouseDown={event => event.preventDefault()} onClick={clearSearchHistory}>Clear all</button></div>
      {searchHistory.map(entry => <div className="search-history-item" key={entry}>
        <button type="button" role="option" onMouseDown={event => event.preventDefault()} onClick={() => commitSearch(entry)}><span aria-hidden="true">◷</span><b>{entry}</b></button>
        <button type="button" className="search-history-remove" onMouseDown={event => event.preventDefault()} onClick={() => removeSearchHistory(entry)} aria-label={"Remove " + entry + " from search history"}>×</button>
      </div>)}
    </div>}
    {showSearchSuggestions && <div id="public-search-suggestions" className="search-suggestions" role="listbox" aria-label="Search suggestions">
      <div className="search-suggestions-header"><strong>Suggestions</strong><span>Use ↑ ↓ then Enter</span></div>
      {searchSuggestions.map((suggestion, index) => <button
        key={suggestion}
        id={"public-search-suggestion-" + index}
        type="button"
        role="option"
        aria-selected={suggestionIndex === index}
        className={suggestionIndex === index ? "active" : ""}
        onMouseDown={event => event.preventDefault()}
        onMouseEnter={() => setSuggestionIndex(index)}
        onClick={() => commitSearch(suggestion)}
      ><span aria-hidden="true">F</span><span className="search-suggestion-text"><b>{suggestion}</b></span><small>File name</small></button>)}
    </div>}
  </div>
  <div className="public-media-filter" aria-label="Media filter"><button className={mediaFilter === "all" ? "active" : ""} onClick={() => setMediaFilter("all")}>All</button><button className={mediaFilter === "images" ? "active" : ""} onClick={() => setMediaFilter("images")}>Images</button><button className={mediaFilter === "videos" ? "active" : ""} onClick={() => setMediaFilter("videos")}>Videos</button></div>
 </header><div className="public-workspace"><PublicSourceTree shareId={id} roots={folders} active={activeFolder} activeTrail={activeTrail} onOpen={(folder, trail) => void open(folder, trail)}/><section className="public-folder-detail"><div className="public-breadcrumb"><span>Shared drive</span>{activeTrail.map((folder, index) => <span key={folder.source_id + folder.folder_id}> / {index === activeTrail.length - 1 ? <b>{folder.name}</b> : <button onClick={() => void open(folder, activeTrail.slice(0, index + 1))}>{folder.name}</button>}</span>)}</div><div className="public-folder-header"><div><h2>{query ? "Search results" : activeFolder?.name || "Shared folders"}</h2><p>{query ? "Matching files in this shared review." : children.length + " items in this folder."}</p></div><span className="public-read-only">View only</span></div>{shareNotice && <p className="public-share-notice" role="status">{shareNotice}</p>}{loadingFolder ? <PublicGridSkeleton/> : <section className="public-grid">{!query && children.filter((item): item is Folder & { kind: "folder" } => item.kind === "folder").map(folder => <button className={"public-card public-folder-card " + (folder.name.startsWith("Amazon") ? "amazon" : folder.name.startsWith("Etsy") ? "etsy" : "")} key={folder.source_id + folder.folder_id} onClick={() => void open(folder, [...activeTrail, folder])}><span className="public-card-thumb public-folder-glyph"><span className="public-folder-icon"/></span><span className="public-card-copy"><b>{folder.name}</b><small>Folder</small><span className="public-status">Discovered</span></span></button>)}{visibleAssets.map(asset => <article className={"public-card" + (mediaAsset(asset) ? " public-media-card" : "") + ((asset.annotation_count || 0) > 0 ? " has-comments" : "")} key={asset.asset_id + asset.source_asset_id} data-review-video-key={videoAsset(asset) ? reviewAssetKey(asset) : undefined} onMouseEnter={() => warmVideoInteraction(asset)} onFocus={() => warmVideoInteraction(asset)}><button type="button" className="public-card-open" onPointerDown={() => warmVideoInteraction(asset)} onClick={() => selectMediaAsset(asset)} aria-label={"Open " + asset.filename}><span className="public-card-info" aria-hidden="true">i</span><span className="public-card-thumb"><PublicAssetThumbnail asset={asset}/></span>{videoAsset(asset) && <span className="public-video-card-badge" aria-hidden="true">▶</span>}<span className="public-card-copy"><b>{asset.filename}</b><small>{imageAsset(asset) ? "Image" : videoAsset(asset) ? "Video" : "File"}</small>{mediaAsset(asset) && <span className="public-card-rating" aria-hidden="true">★★★★★</span>}</span></button>{mediaAsset(asset) && <><span className={"public-card-actions" + ((asset.annotation_count || 0) > 0 ? " has-comments" : "")} aria-label={"Actions for " + asset.filename}><button type="button" className="public-card-comment-action" onClick={() => openNote(asset)} aria-label={(asset.annotation_count || 0) > 0 ? (asset.annotation_count || 0) + " comments on " + asset.filename : "Add note to " + asset.filename} title={(asset.annotation_count || 0) > 0 ? (asset.annotation_count || 0) + " comments" : "Add note"}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.5a7.5 7.5 0 0 1-7.8 7.5 8.4 8.4 0 0 1-3.4-.7L4 20l1.4-4A7.1 7.1 0 0 1 4 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z"/></svg>{(asset.annotation_count || 0) > 0 && <span className="public-card-comment-count" aria-hidden="true">{(asset.annotation_count || 0) > 99 ? "99+" : asset.annotation_count}</span>}</button></span><span className="public-card-hover-actions" role="group" aria-label={"Quick actions for " + asset.filename}>{boot.allow_download ? <a className="public-card-download-action" href={api.downloadUrl(id, asset)} download={asset.filename} aria-label={"Download " + asset.filename} title="Download"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></svg></a> : <button type="button" className="public-card-download-action" disabled aria-label={"Download disabled for " + asset.filename} title="Downloads are disabled for this review"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14"/></svg></button>}<button type="button" className="public-card-share-action" onClick={() => void copyReviewLink()} aria-label="Copy review link" title="Copy review link"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.8 7.5-4.4M8.2 13.2l7.5 4.4"/></svg></button></span></>}</article>)}{!children.length && !visibleAssets.length && <p className="public-empty">No shared items in this folder.</p>}{!query && nextOffset !== null && <><div ref={loadMoreRef} className="public-load-more-sentinel" aria-hidden="true"/>{loadingMore && <span className="public-load-more">Loading more shared items…</span>}</>}</section>}</section></div><PublicReviewHistory entries={viewHistory} comments={commentActivity} commentsLoading={commentsLoading} activeTab={activityTab} currentKey={currentMediaKey || undefined} onSelectTab={selectActivityTab} onDismiss={() => setActivityTab(null)} onOpenHistory={openHistoryEntry} onOpenComment={openCommentEntry} onClearHistory={clearHistoryItems}/>{current && <div className="public-review-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) closeViewer(); }}>
  <section className="public-viewer" role="dialog" aria-modal="true" aria-label={"Review " + current.filename} onMouseDown={event => event.stopPropagation()}>
    <button type="button" className="public-viewer-close" onClick={closeViewer} aria-label="Close" title="Close"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
    <div ref={mediaRef} className={"public-media-stage" + (pinMode ? " pin-mode" : "")} onClick={clickMedia}>
      <div className="public-media-position" aria-label={`Media ${currentIndex + 1} of ${viewerAssets.length}`} role="status">
        {mediaPosition.keys.map(key => <span key={key} className={key === currentMediaKey ? "active" : ""} aria-hidden="true"/>)}
      </div>
      {currentIndex > 0 && <button type="button" className="public-media-nav public-media-nav-previous" aria-label="Previous asset" title="Previous asset" onClick={event => { event.stopPropagation(); previousAsset(); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.5 5-7 7 7 7"/></svg></button>}
      {videoAsset(current) ? <><video ref={videoRef} key={currentMediaKey} data-media-key={currentMediaKey} className="public-video-preview" preload="auto" poster={current.thumbnail_url} src={activeVideoSource} muted={videoMuted} playsInline onLoadedMetadata={event => { captureVideoMetadata(event.currentTarget); if (isActiveVideo(event.currentTarget) && videoResumeAt.current > 0) { const resume = videoResumeAt.current; videoResumeAt.current = 0; event.currentTarget.currentTime = Number.isFinite(event.currentTarget.duration) ? Math.min(resume, Math.max(0, event.currentTarget.duration - 0.05)) : resume; } }} onLoadedData={event => { if (isActiveVideo(event.currentTarget)) setVideoBuffering(false); }} onError={event => retryVideoTicket(event.currentTarget)} onCanPlay={event => { if (isActiveVideo(event.currentTarget)) { setVideoReady(true); setVideoBuffering(false); autoplayWhenReady(event.currentTarget); } }} onWaiting={event => { if (isActiveVideo(event.currentTarget) && videoReady) setVideoBuffering(true); }} onPlaying={event => { if (isActiveVideo(event.currentTarget)) { setAutoplayPending(false); setVideoPlaying(true); setVideoBuffering(false); } }} onPause={event => { if (isActiveVideo(event.currentTarget)) setVideoPlaying(false); }}>Your browser cannot play this video.</video><button type="button" className="public-video-mute" aria-label={videoMuted ? "Bật âm thanh" : "Tắt âm thanh"} title={videoMuted ? "Bật âm thanh" : "Tắt âm thanh"} aria-pressed={videoMuted} onClick={toggleVideoMuted}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4h4l5 4V6L8 10H4Z"/>{videoMuted ? <><path d="m17 9 4 6"/><path d="m21 9-4 6"/></> : <><path d="M16 9.2a4 4 0 0 1 0 5.6"/><path d="M18.5 6.8a7.3 7.3 0 0 1 0 10.4"/></>}</svg></button>{(!videoReady || videoBuffering || autoplayPending) && <span className="public-video-loading" aria-label="Loading video"><i/></span>}{videoReady && !videoPlaying && !videoBuffering && !autoplayPending && <button type="button" className="public-video-play" onClick={event => { event.stopPropagation(); toggleVideo(); }} aria-label="Play video"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 9 6-9 6z"/></svg></button>}</> : <img key={currentMediaKey} ref={imageRef} onLoad={event => { refreshGeometry(); const image = event.currentTarget; if (image.naturalWidth && image.naturalHeight) setMediaDetails({ width: image.naturalWidth, height: image.naturalHeight, duration: null }); }} src={current.preview_url} alt={current.filename} loading="eager" decoding="async"/>}
      {currentIndex >= 0 && currentIndex < viewerAssets.length - 1 && <button type="button" className="public-media-nav public-media-nav-next" aria-label="Next asset" title="Next asset" onClick={event => { event.stopPropagation(); nextAsset(); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9.5 5 7 7-7 7"/></svg></button>}
      {imageAsset(current) && geometry && pins.map((note, index) => { const position = pinPosition(geometry.box, geometry.width, geometry.height, note.anchor_x!, note.anchor_y!); return position ? <button key={note.id} className={"public-pin" + (selected === note.id ? " selected" : "")} style={{ left: position.x - geometry.box.left, top: position.y - geometry.box.top }} aria-label={"Open comment " + (index + 1)} onClick={event => { event.stopPropagation(); select(note); }}>{index + 1}</button> : null; })}
      {imageAsset(current) && editor?.pin && geometry && (() => { const position = pinPosition(geometry.box, geometry.width, geometry.height, editor.pin.x, editor.pin.y); return position ? <span className="public-pin pending" style={{ left: position.x - geometry.box.left, top: position.y - geometry.box.top }}>+</span> : null; })()}
    </div>
    <aside className="public-review-sidebar">
      <header className="public-review-header"><div className="public-review-heading"><h2>Review</h2><div className="public-review-metadata" aria-label="Media information"><span><small>Resolution</small><b>{reviewResolution}</b></span><span><small>Duration</small><b>{reviewDuration}</b></span><span><small>Ratio</small><b>{reviewRatio}</b></span></div></div></header>
      <div className="public-review-feed" aria-busy={notesLoading}>{notesLoading ? <div className="public-review-notes-loading" role="status" aria-label="Loading comments"><span/><span/><span/></div> : roots.length ? roots.map(note => <article id={"annotation-" + note.id} className={"public-comment" + (selected === note.id ? " selected" : "")} key={note.id} onClick={() => select(note)}><span className={"public-comment-avatar public-avatar-tone-" + avatarTone(note.author.display_name)} aria-hidden="true">{avatarInitials(note.author.display_name)}</span><div className="public-comment-body"><b>{note.author.display_name}</b><RichAnnotation document={note.content_json || emptyDocument()}/><footer className="public-comment-footer"><time dateTime={note.created_at}>{commentTime(note.created_at)}</time>{boot.allow_comments && <div className="public-note-actions"><button type="button" className={editor?.parent === note.id ? "active" : undefined} aria-label={"Reply to " + note.author.display_name} title="Reply" aria-pressed={editor?.parent === note.id} onClick={event => { event.stopPropagation(); beginReply(note); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 8 4 12l5 4v-3h5a5 5 0 0 1 5 5c1-5-1-9-6-9H9V8Z"/></svg></button>{note.can_edit && <button type="button" aria-label="Edit" title="Edit" onClick={event => { event.stopPropagation(); setEditor({ edit: note }); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16.5-.8 4.3 4.3-.8L19 8.5 15.5 5 4 16.5Z"/><path d="m13.8 6.7 3.5 3.5"/></svg></button>}{note.can_delete && <button type="button" className="public-note-delete" aria-label="Delete" title="Delete" onClick={event => { event.stopPropagation(); void remove(note); }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5"/></svg></button>}</div>}</footer><div className="public-comment-replies">{visibleNotes.filter(value => value.parent_annotation_id === note.id).map(reply => <div id={"annotation-" + reply.id} className={"public-reply public-comment-reply" + (selected === reply.id ? " selected" : "")} key={reply.id} onClick={() => select(reply)}><span className={"public-comment-avatar public-avatar-tone-" + avatarTone(reply.author.display_name)} aria-hidden="true">{avatarInitials(reply.author.display_name)}</span><div className="public-comment-body"><b>{reply.author.display_name}</b><RichAnnotation document={reply.content_json || emptyDocument()}/><time dateTime={reply.created_at}>{commentTime(reply.created_at)}</time></div></div>)}</div></div></article>) : <p className="public-review-empty">No comments yet</p>}</div>
      <div ref={composerRef} className={"public-review-composer" + (replyTarget ? " is-replying" : "")}>{boot.allow_comments ? <>{replyTarget && <div className="public-reply-context" role="status" aria-label={"Replying to " + replyTarget.author.display_name}><span className="public-reply-context-accent" aria-hidden="true"/><div className="public-reply-context-copy"><span>Đang trả lời <b>{replyTarget.author.display_name}</b></span><small>{commentExcerpt(replyTarget)}</small></div><button type="button" className="public-reply-cancel" aria-label="Hủy trả lời" title="Hủy trả lời" onClick={() => setEditor(undefined)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg></button></div>}<AnnotationEditor key={composerKey} initial={editor?.edit?.content_json} placeholder={replyTarget ? "Trả lời " + replyTarget.author.display_name + "..." : "Bình luận..."} submitLabel={editor?.edit ? "Lưu" : replyTarget ? "Trả lời" : "Đăng"} onSubmit={save}/></> : <p>Comments are disabled for this review.</p>}</div>
    </aside>
  </section>
</div>}</main>;
}