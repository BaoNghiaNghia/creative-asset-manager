import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";
import { fetchAccessIdentity } from "../../features/access_management";
import {
  spyFetch, readableTime,
  type SpyTab, type Page, type Summary, type ExtractionOverview, type ImageAgentStatus, type Listing,
  type Keyword, type Trademark, type SpyLog, type Mark, type AgentStatus,
  type ObservedDaysSummary, type ObservedDayBucket, type DayGroupsSummary,
} from "./api";
import "./EtsySpyPage.css";
import { KeywordTreeView } from "./KeywordTreeView";

const TABS: { id: SpyTab; title: string; subtitle: string }[] = [
  { id: "products", title: "Products", subtitle: "Eligible Etsy listings" },
  { id: "keywords", title: "Keyword Tree", subtitle: "Discovery branches" },
  { id: "trademarks", title: "Trademark", subtitle: "Cached TM evidence" },
  { id: "logs", title: "System Logs", subtitle: "Agent diagnostics" },
];
const SORTS: Record<SpyTab, readonly { id: string; text: string }[]> = {
  products: [
    { id: "pageTitle", text: "Product / Etsy" }, { id: "keywordSms", text: "Keyword SMS" },
    { id: "badge", text: "Etsy Badge + TM" },
    { id: "bought24h", text: "Bought / 24h" }, { id: "views24h", text: "Views / 24h" },
    { id: "inCarts", text: "Carts" }, { id: "observedAt", text: "Observed" },
  ],
  keywords: [
    { id: "keyword", text: "Keyword" }, { id: "depth", text: "Depth" },
    { id: "priority", text: "Priority" }, { id: "status", text: "Status" },
    { id: "matchedCount", text: "Listings" },
  ],
  trademarks: [
    { id: "keyword", text: "Trademark / Keyword" }, { id: "status", text: "Check status" },
    { id: "liveCount", text: "Evidence · LIVE / DEAD / UNKNOWN" }, { id: "checkedAt", text: "Last checked" },
  ],
  logs: [
    { id: "at", text: "Time (GMT+7)" }, { id: "level", text: "Level" },
    { id: "source", text: "Source" }, { id: "phase", text: "Phase" },
    { id: "keyword", text: "Keyword" },
  ],
};
type Options = {
  tab: SpyTab; page: number; pageSize: number; q: string;
  pickState: "eligible" | "picked" | "later";
  blacklistView: boolean; winKeywordsView: boolean;
  observedDay: string; group: string;
  sessionId: string; source: string; badge: string; tm: string;
  status: string; level: string; phase: string; sort: string; dir: "asc" | "desc";
};
export function vietnamCalendarDay(moment: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(moment);
  const value = (part: string) => parts.find(item => item.type === part)?.value || "00";
  return value("year") + "-" + value("month") + "-" + value("day");
}

export function recentObservedDays(today: string): string[] {
  const start = new Date(today + "T00:00:00Z");
  return Array.from({ length: 10 }, (_, index) => {
    const at = new Date(start);
    at.setUTCDate(start.getUTCDate() - index);
    return at.toISOString().slice(0, 10);
  });
}

const baseOptions: Options = {
  tab: "products", page: 1, pageSize: 50, q: "", pickState: "eligible",
  blacklistView: false, winKeywordsView: false, observedDay: vietnamCalendarDay(), group: "",
  sessionId: "", source: "", badge: "", tm: "", status: "", level: "", phase: "",
  sort: "observedAt", dir: "desc",
};
function fromLocation(): Options {
  if (typeof window === "undefined") return baseOptions;
  const p = new URLSearchParams(window.location.search);
  const tab = TABS.find(t => t.id === p.get("tab"))?.id || "products";
  return {
    ...baseOptions, tab,
    pickState: p.get("pick_state") === "picked" ? "picked"
      : p.get("pick_state") === "later" ? "later" : "eligible",
    blacklistView: tab === "products" && p.get("panel") === "blacklist",
    winKeywordsView: tab === "products" && p.get("panel") === "win-keywords",
    observedDay: p.get("observed_day") === "all" ? "all"
      : /^\d{4}-\d{2}-\d{2}$/.test(p.get("observed_day") || "")
        ? p.get("observed_day")! : vietnamCalendarDay(),
    group: (p.get("group") || "").slice(0,40),
    page: Math.max(1, Math.min(100000, Number(p.get("page")) || 1)),
    pageSize: [50, 100, 200].includes(Number(p.get("page_size"))) ? Number(p.get("page_size")) : 50,
    q: p.get("q") || "", sessionId: p.get("session_id") || "",
    source: p.get("source") || "", badge: p.get("badge") || "",
    tm: p.get("tm") || "", status: p.get("status") || "",
    level: p.get("level") || "", phase: p.get("phase") || "",
    sort: p.get("sort") || (tab === "products" ? "observedAt" : tab === "logs" ? "at" : "updatedAt"),
    dir: p.get("dir") === "asc" ? "asc" : "desc",
  };
}
function queryFor(o: Options): URLSearchParams {
  const query = new URLSearchParams();
  query.set("page", String(o.page));
  query.set("page_size", String(o.pageSize));
  if (o.q.trim()) query.set("q", o.q.trim());
  if (o.tab === "products" && o.pickState !== "eligible" && !o.blacklistView && !o.winKeywordsView)
    query.set("pick_state", o.pickState);
  if (o.tab === "products" && !o.blacklistView && !o.winKeywordsView) {
    query.set("observed_day", o.observedDay);
    if (o.group && o.observedDay !== "all") query.set("group", o.group);
  }
  if (o.tab === "products" && o.blacklistView) query.set("panel", "blacklist");
  if (o.tab === "products" && o.winKeywordsView) query.set("panel", "win-keywords");
  if (o.sessionId) query.set("session_id", o.sessionId);
  if (o.source) query.set("source", o.source);
  if (o.badge) query.set("badge", o.badge);
  if (o.tm) query.set("tm", o.tm);
  if (o.status) query.set("status", o.status);
  if (o.level) query.set("level", o.level);
  if (o.phase) query.set("phase", o.phase);
  if (o.sort) query.set("sort", o.sort);
  query.set("dir", o.dir);
  return query;
}
function apiQuery(o: Options): URLSearchParams {
  const result = queryFor(o);
  result.delete("panel");
  result.delete("source");
  result.delete("badge");
  result.delete("tm");
  result.delete("status");
  result.delete("level");
  result.delete("phase");
  if (o.tab === "products") {
    if (result.get("observed_day") === "all") result.delete("observed_day");
    for (const key of ["source", "badge", "tm"] as const) if (o[key]) result.set(key, o[key]);
  } else if (o.tab === "keywords") {
    if (o.status) result.set("status", o.status);
  } else if (o.tab === "trademarks") {
    result.delete("session_id");
    if (o.status) result.set("status", o.status);
  } else {
    if (o.level) result.set("level", o.level);
    if (o.source) result.set("source", o.source);
    if (o.phase) result.set("phase", o.phase);
  }
  return result;
}
function tmClass(status: string) {
  return status === "LIVE" ? "live" : status === "DEAD" ? "dead"
    : status === "no_match" ? "no-match"
    : status === "unverified" ? "unverified" : "neutral";
}
function tmDescription(status: string, deadMatches = 0) {
  return status === "no_match" ? "TM: No match*"
    : status === "DEAD" ? `TM DEAD${deadMatches ? " · " + deadMatches : ""}`
    : status === "pending" ? "Pending"
    : status === "unverified" ? "Unverified" : status;
}
const SUMMARY_ICONS = [
  "M20 12.5 12.5 20a2 2 0 0 1-2.8 0l-6.7-6.7a2 2 0 0 1-.6-1.4V4a2 2 0 0 1 2-2h7.9a2 2 0 0 1 1.4.6l6.3 6.3a2 2 0 0 1 0 2.8ZM7.5 7.5h.01",
  "M4 12.5 9 17l11-11",
  "M12 7v5l3 2M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20",
  "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20M5 5l14 14",
  "m12 2 3.2 6.5 7.2 1-5.2 5.1 1.2 7.3-6.4-3.4L5.6 21l1.2-7.3L1.6 9.6l7.2-1L12 2Z",
  "M15.5 7.5a5 5 0 1 0-1 4.2L21 18v3h-3v-3h-3v-3l-1.7-1.7",
  "m12 2 10 5-10 5L2 7l10-5Zm-10 10 10 5 10-5M2 17l10 5 10-5",
  "M2 3h2l2.5 11h12L21 6H5M9 20h.01M18 20h.01",
  "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 5v5l3 2",
  "M10.3 3.5 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.5a2 2 0 0 0-3.4 0ZM12 9v4m0 4h.01",
  "M12 9v4m0 4h.01M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20",
] as const;
function safeMarks(matches: Mark[] | null | undefined): Mark[] {
  // Older Windows snapshots may omit matched evidence entirely or store null.
  // Runtime normalization is necessary even when the API type expects an array.
  return Array.isArray(matches)
    ? matches.filter((mark): mark is Mark => Boolean(mark && typeof mark === "object"))
    : [];
}
function EvidenceMarks({ matches }: { matches?: Mark[] | null }) {
  const validMarks = safeMarks(matches);
  if (!validMarks.length) return <span className="etsy-muted">No trademark evidence attached</span>;
  return <div className="etsy-mark-items">
    {validMarks.slice(0, 30).map((item, index) => <div key={(item.serialNumber || item.markText) + index}>
      <strong>{item.markText || "Unnamed mark"}</strong>
      <span>{item.liveStatus || "UNKNOWN"}</span>
      {item.serialNumber && <a href={"https://tsdr.uspto.gov/#caseNumber=" + encodeURIComponent(item.serialNumber) + "&caseSearchType=US_APPLICATION&caseType=DEFAULT&searchType=statusSearch"} target="_blank" rel="noopener noreferrer">
        USPTO #{item.serialNumber}
      </a>}
    </div>)}
  </div>;
}
function safeEtsyImageUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 1600) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" ||
        !["i.etsystatic.com", "img.etsystatic.com"].includes(parsed.hostname) ||
        parsed.username || parsed.password || parsed.port ||
        parsed.search || parsed.hash) return null;
    return parsed.href;
  } catch { return null; }
}

function ProductThumbnail({ item }: { item: Listing }) {
  const thumb = safeEtsyImageUrl(item.thumbnailUrl ?? item.metadata?.thumbnailUrl);
  const full = safeEtsyImageUrl(item.fullImageUrl ?? item.metadata?.fullImageUrl) ?? thumb;
  const [broken, setBroken] = useState(false);
  useEffect(() => { setBroken(false); }, [thumb]);
  if (!thumb || broken) return <span className="etsy-product-glyph" aria-hidden="true">E</span>;
  return <a className="etsy-product-thumbnail-link" href={full ?? item.url ?? "#"}
    target="_blank" rel="noopener noreferrer"
    title="View full Etsy product image" aria-label={"View full image of " + item.pageTitle}>
    <img className="etsy-product-thumbnail" src={thumb} alt=""
      width="68" height="68" loading="lazy" decoding="async"
      onError={() => setBroken(true)} />
  </a>;
}

function ProductRow({ item, canPick, busy, onPick, onLater }: {
  item: Listing; canPick: boolean; busy: boolean;
  onPick: (item: Listing) => void; onLater: (item: Listing) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [imagePreview, setImagePreview] = useState<{
    left: number; bottom: number; width: number; maxHeight: number; imageMaxHeight: number;
  } | null>(null);
  const [fullImageFailed, setFullImageFailed] = useState(false);
  const previewThumb = safeEtsyImageUrl(item.thumbnailUrl ?? item.metadata?.thumbnailUrl);
  const previewFull = safeEtsyImageUrl(item.fullImageUrl ?? item.metadata?.fullImageUrl);
  const previewSource = fullImageFailed ? previewThumb : previewFull ?? previewThumb;
  // Full image is loaded only on hover in a viewport-anchored body portal.
  // Never couple its position or visibility to the sidebar/navigation height.
  useEffect(() => {
    if (!imagePreview) return;
    const hide = () => setImagePreview(null);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [imagePreview]);
  const showImagePreview = (event: React.PointerEvent<HTMLTableRowElement>) => {
    if (event.pointerType === "touch" || !previewSource) return;
    // The old 248px sidebar-sized preview is enlarged by exactly 15%, but
    // its anchor is the viewport bottom-left (not the navigation rail).
    const gutter = 16;
    const width = Math.min(Math.round(248 * 1.15), window.innerWidth - gutter * 2);
    const maxHeight = Math.min(Math.round(440 * 1.15), window.innerHeight - gutter * 2);
    if (width < 160 || maxHeight < 172) return;
    setImagePreview({
      left: gutter, bottom: gutter, width, maxHeight,
      imageMaxHeight: Math.min(Math.round(360 * 1.15), maxHeight - 82),
    });
  };
  const sms = Array.isArray(item.keywordSms) ? item.keywordSms :
    (Array.isArray(item.metadata?.keywordSms) ? item.metadata.keywordSms : []);
  const smsText = sms.map(value => typeof value === "string" ? value.trim() : "")
    .filter(Boolean).join(", ");
  const smsStatus = item.keywordSmsStatus || item.metadata?.keywordSmsStatus || "not_checked";
  const tmMatches = safeMarks(item.tmMatches);
  const hasMarks = tmMatches.length > 0;
  const deadMatches = tmMatches.filter(mark => mark.liveStatus?.toUpperCase() === "DEAD").length;
  const tmTooltip = [item.tmSource || "TM status", item.tmCheckedAt ? readableTime(item.tmCheckedAt) : "",
    item.tmSourceAsOf ? "Source as of " + readableTime(item.tmSourceAsOf) : ""].filter(Boolean).join(" · ");
  const tmLabel = tmDescription(item.tm, deadMatches);
  return <>
    <tr className="etsy-product-row"
      onPointerEnter={showImagePreview}
      onPointerLeave={() => setImagePreview(null)}
      onPointerCancel={() => setImagePreview(null)}>
    <td className="etsy-title">
      <div className="etsy-product-identity">
        <ProductThumbnail item={item} />
        <div className="etsy-product-copy">
          <strong title={item.pageTitle}>{item.pageTitle}</strong>
          <div className="etsy-title-meta">
            <small>#{item.listingKey}</small>
            {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer">View on Etsy <span aria-hidden="true">↗</span></a>}
          </div>
          {item.picked && <small className="etsy-extraction-status"
            title="Extracted artwork is for internal review until fidelity and usage rights are verified">
            Extraction · {({
              pending: "Queued", retry: "Retry", processing: "Extracting",
              needs_review: "Review PNG", needs_ai: "Needs AI",
              failed: "Failed", cancelled: "Cancelled",
              source_missing: "Source image missing", disabled: "Worker inactive",
            } as Record<string, string>)[item.extraction?.status || "source_missing"] ||
              item.extraction?.status || "Pending"}
          </small>}
          {item.picked && item.extraction?.status === "needs_review" && item.extraction.job_id &&
            <a className="etsy-extraction-open"
              href={"/api/v1/etsy-spy/image-agents/assets/" + encodeURIComponent(item.extraction.job_id)}
              target="_blank" rel="noopener noreferrer"
              title="Open transparent PNG for manual artwork and rights review">
              View PNG
            </a>}
          {item.later && <small className="etsy-later-match-reason"
            title={item.laterReason || "Grouped by Later keyword theme"}>
            {item.laterManual ? "Later mẫu" : "Tự gom"} · {item.laterReason || "Chủ đề thiết kế liên quan"}
          </small>}
        </div>
      </div>
    </td>
    <td className="etsy-sms-cell">
      {smsText ? <div className="etsy-sms-preview" title={smsText}>
        <span className="etsy-sms-list">{smsText}</span>
      </div> : <span className="etsy-muted"
        title={smsStatus === "not_checked" ? "Chưa kiểm tra gợi ý Etsy" :
          "Chưa có gợi ý hợp lệ · " + smsStatus}>
        {smsStatus === "not_checked" ? "Chưa kiểm tra" : "None"}
      </span>}
    </td>
    <td><div className="etsy-badge-stack">
      {item.badge || item.searchBadge ? <span className={"etsy-pill etsy-demand " + (/popular/i.test(item.badge || item.searchBadge || "") ? "popular" : "bestseller")}>{item.badge || item.searchBadge}</span> : <span className="etsy-muted">—</span>}
      {hasMarks ? <button type="button" className={"etsy-pill etsy-tm-badge tm-" + tmClass(item.tm)}
        title={tmTooltip} onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
        {tmLabel}
      </button> : <span className={"etsy-pill etsy-tm-badge tm-" + tmClass(item.tm)} title={tmTooltip}>{tmLabel}</span>}
      {item.tm === "no_match" && <small className="etsy-tm-disclaimer">
        No matches found in source data; this does not mean trademark clearance.
      </small>}
    </div>
    {expanded && hasMarks && <EvidenceMarks matches={tmMatches} />}
    </td>
    <td className="etsy-num">{item.bought24h ?? "—"}</td>
    <td className="etsy-num">{item.views24h ?? "—"}</td>
    <td className="etsy-num">{item.inCarts === 20 ? "20+" : item.inCarts ?? "—"}</td>
    <td className="etsy-time">{readableTime(item.observedAt)}</td>
    <td className="etsy-action-cell">
      <div className="etsy-row-actions">
        <button type="button"
          className={"etsy-pick-button" + (item.picked ? " is-picked" : "")}
          aria-label={(item.picked ? "Unpick " : "Pick ") + item.pageTitle}
          title={canPick ? (item.picked ? "Unpick · return to Eligible listings" : "Pick · move to Picked") : "Manage permission required"}
          aria-pressed={Boolean(item.picked)} disabled={!canPick || busy}
          onClick={() => onPick(item)}>
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12.5 10 17l9-10" />
          </svg>
          <span>{item.picked ? "Picked" : "Pick"}</span>
        </button>
        {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer"
          aria-label={"Open Etsy listing: " + item.pageTitle} title="Open on Etsy">↗</a>}
        <button type="button"
          className={"etsy-later-button" + (item.later ? " is-later" : "")}
          aria-label={(item.later
            ? (item.laterManual ? "Remove Later seed " : "Dismiss Later suggestion ")
            : "Later ") + item.pageTitle}
          title={!canPick ? "Manage permission required"
            : item.picked ? "Unpick first to move to Later"
            : item.later ? (item.laterManual
                ? "Remove this Later topic and recompute related products"
                : "Hide this suggestion; future sync will not bring it back")
              : "Later · save as design theme and group related future products"}
          aria-pressed={Boolean(item.later)} disabled={!canPick || busy || item.picked}
          onClick={() => onLater(item)}>
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="9"/>
            <path d="M12 7v5l3 2"/>
          </svg>
          <span>Later</span>
        </button>
      </div>
    </td>
    </tr>
    {imagePreview && previewSource && createPortal(
      <div className="etsy-product-hover-preview" role="tooltip"
        style={{left: imagePreview.left, bottom: imagePreview.bottom,
          width: imagePreview.width, maxHeight: imagePreview.maxHeight}}
        data-listing-key={item.listingKey}>
        <span className="etsy-product-preview-label">Ảnh sản phẩm Etsy</span>
        <img src={previewSource} alt={item.pageTitle} decoding="async"
          style={{width: imagePreview.width, maxHeight: imagePreview.imageMaxHeight}}
          onError={() => {
            if (!fullImageFailed && previewThumb && previewFull !== previewThumb) {
              setFullImageFailed(true);
            } else {
              setImagePreview(null);
            }
          }} />
        <span className="etsy-product-preview-title">
          <span className="etsy-product-preview-title-text">{item.pageTitle}</span>
        </span>
      </div>, document.body,
    )}
  </>;
}

function TrademarkRow({ item }: { item: Trademark }) {
  const [expanded, setExpanded] = useState(false);
  const matches = safeMarks(item.matched);
  return <tr>
    <td><strong>{item.keyword || item.title || "—"}</strong>
      {item.title && item.keyword && <small>{item.title}</small>}
      <small>{item.kind} · {item.source || "source unknown"}</small>
      {matches.length > 0 && <button type="button" className="etsy-details-trigger"
        aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? "Hide" : "Show"} {matches.length} marks
      </button>}
      {expanded && matches.length > 0 && <EvidenceMarks matches={matches} />}
    </td>
    <td><span className={"etsy-pill tm-" + tmClass(item.status || "")}>{item.status || "pending"}</span>
      <small>{item.risk || "Not verified"}</small>
    </td>
    <td><div className="etsy-tm-counts" aria-label="Trademark evidence counts">
      <span className="etsy-tm-count-live" title="LIVE trademark records"><strong>{item.liveCount ?? "—"}</strong> LIVE</span>
      <span className="etsy-tm-count-dead" title="DEAD trademark records"><strong>{item.deadCount ?? "—"}</strong> DEAD</span>
      <span className="etsy-tm-count-unknown" title="Unknown trademark records"><strong>{item.unknownCount ?? "—"}</strong> Unknown</span>
    </div></td>
    <td className="etsy-checked-at"><strong>{readableTime(item.checkedAt)}</strong><small>Source: {readableTime(item.sourceAsOf)}</small></td>
  </tr>;
}
function LogRow({ item }: { item: SpyLog }) {
  const [expanded, setExpanded] = useState(false);
  return <tr>
    <td className="etsy-time">{readableTime(item.at)}</td>
    <td><span className={"etsy-pill log-" + (item.level || "info")}>{item.level || "info"}</span></td>
    <td>{item.source || "—"}</td><td>{item.phase || "—"}</td>
    <td>{item.keyword || "—"}</td>
    <td className="etsy-title"><strong>{item.message || "—"}</strong>
      <small>{item.category || "—"} · {item.action || "—"}</small>
      {item.stack && <><button className="etsy-details-trigger" onClick={() => setExpanded(!expanded)}>
        {expanded ? "Hide" : "Show"} stack
      </button>{expanded && <pre className="etsy-evidence">{item.stack}</pre>}</>}
    </td>
  </tr>;
}

export function EtsySpyPage() {
  const [options, setOptions] = useState<Options>(fromLocation);
  const [typedSearch, setTypedSearch] = useState(options.q);
  const [blacklistInput, setBlacklistInput] = useState("");
  const [blacklist, setBlacklist] = useState<string[]>([]);
  const [blacklistBusy, setBlacklistBusy] = useState(false);
  const [blacklistError, setBlacklistError] = useState("");
  const [winKeywordInput, setWinKeywordInput] = useState("");
  const [winKeywords, setWinKeywords] = useState<string[]>([]);
  const [winKeywordBusy, setWinKeywordBusy] = useState(false);
  const [winKeywordError, setWinKeywordError] = useState("");
  const [pickBusyKey, setPickBusyKey] = useState<string | null>(null);
  const [pickError, setPickError] = useState("");
  const [extractionBackfillBusy, setExtractionBackfillBusy] = useState(false);
  const [extractionBackfillMessage, setExtractionBackfillMessage] = useState("");
  const [laterBusyKey, setLaterBusyKey] = useState<string | null>(null);
  const [laterError, setLaterError] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [extraction, setExtraction] = useState<ExtractionOverview | null>(null);
  const [clockDay, setClockDay] = useState(vietnamCalendarDay);
  const [observedDays, setObservedDays] = useState<ObservedDaysSummary | null>(null);
  const [dayGroups, setDayGroups] = useState<DayGroupsSummary | null>(null);
  const [dayGroupError, setDayGroupError] = useState(false);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [imageAgents, setImageAgents] = useState<ImageAgentStatus[]>([]);
  const [imagePairName, setImagePairName] = useState("DESKTOP-3FR0LF0");
  const [imagePairOpen, setImagePairOpen] = useState(false);
  const [imagePairBusy, setImagePairBusy] = useState(false);
  const [imagePairError, setImagePairError] = useState("");
  const [imagePairSecret, setImagePairSecret] = useState<{ agent_id: string; token: string } | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [tableResult, setTableResult] = useState<{
    requestKey: string; data: Page<Listing | Keyword | Trademark | SpyLog> | null;
  } | null>(null);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [tmText, setTmText] = useState("");
  const [tmMode, setTmMode] = useState<"exactMatch" | "partialMatch">("exactMatch");
  const [tmResult, setTmResult] = useState<Mark[] | null>(null);
  const [tmLookupError, setTmLookupError] = useState("");
  const [pairing, setPairing] = useState<{ agent_id: string; token: string } | null>(null);
  const [pairName, setPairName] = useState("DESKTOP-3FR0LF0");
  const [pairError, setPairError] = useState("");
  const [pairOpen, setPairOpen] = useState(false);
  const [pairBusy, setPairBusy] = useState(false);
  // Never render a page for one tab/filter using rows from another request.
  // useEffect runs after render, so merely setting loading=true in the effect
  // cannot prevent an intermediate render with the previous tab's row shape.
  const requestKey = options.tab + (options.blacklistView ? ":blacklist"
    : options.winKeywordsView ? ":win-keywords" : ":records") +
    "?" + apiQuery(options).toString() + "#" + refresh;
  const rows = tableResult?.requestKey === requestKey ? tableResult.data : null;
  const loading = fetching || tableResult?.requestKey !== requestKey;
  const update = useCallback((changes: Partial<Options>) => {
    setOptions(old => ({ ...old, ...changes, page: changes.page ?? 1 }));
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => update({ q: typedSearch }), 360);
    return () => window.clearTimeout(timer);
  }, [typedSearch, update]);
  useEffect(() => {
    const query = queryFor(options);
    query.set("tab", options.tab);
    window.history.replaceState(window.history.state, "", "/etsy-spy?" + query.toString());
  }, [options]);
  useEffect(() => {
    let alive = true;
    Promise.all([
      spyFetch<Summary>("/summary"),
      spyFetch<ExtractionOverview>("/image-agents/overview").catch(() => null),
      spyFetch<{ items: ImageAgentStatus[] }>("/image-agents").catch(() => ({ items: [] })),
      spyFetch<{ items: AgentStatus[] }>("/agents"),
      spyFetch<{ phrases: string[] }>("/blacklist"),
      spyFetch<{ keywords: string[] }>("/win-keywords"),
      fetchAccessIdentity(),
    ]).then(([overview, extractionOverview, currentImageAgents, currentAgents, rules, winList, identity]) => {
      if (!alive) return;
      setSummary(overview); setExtraction(extractionOverview); setAgents(currentAgents.items);
      setImageAgents(currentImageAgents.items);
      setBlacklist(Array.isArray(rules.phrases) ? rules.phrases : []);
      setWinKeywords(Array.isArray(winList.keywords) ? winList.keywords : []);
      setCanManage(identity.permissions.includes("etsy_spy.manage"));
    }).catch(e => { if (alive) setError(e instanceof Error ? e.message : "Cannot load overview"); });
    return () => { alive = false; };
  }, [refresh]);
  // Date and theme counts must belong to the currently selected product
  // parent, never to a stale Eligible fetch after switching to Picked/Later.
  const facetScope = options.pickState;
  const showProductFacets = options.tab === "products" &&
    !options.blacklistView && !options.winKeywordsView;
  const facetDay = options.observedDay === "all" ? null : options.observedDay;
  useEffect(() => {
    if (!showProductFacets) return;
    let alive = true;
    spyFetch<ObservedDaysSummary>("/listings/observed-days?pick_state=" + facetScope)
      .then(response => { if (alive) setObservedDays(response); })
      .catch(() => { if (alive) setObservedDays(null); });
    return () => { alive = false; };
  }, [facetScope, showProductFacets, refresh]);
  useEffect(() => {
    if (!showProductFacets || !facetDay) return;
    let alive = true;
    setDayGroupError(false);
    spyFetch<DayGroupsSummary>("/listings/observed-day-groups?day=" +
      encodeURIComponent(facetDay) + "&pick_state=" + facetScope)
      .then(result => { if (alive) setDayGroups(result); })
      .catch(() => { if (alive) { setDayGroups(null); setDayGroupError(true); } });
    return () => { alive = false; };
  }, [facetDay, facetScope, showProductFacets, refresh]);
  useEffect(() => {
    // Classification is computed from every new Windows Spy sync in SQLite/PG.
    // While this dashboard stays open, check lightweight day aggregates once
    // per minute; refresh the table and badges only if the data has changed.
    // Hidden tabs do no polling; focusing the page checks immediately.
    if (!showProductFacets || !facetDay || !observedDays ||
        observedDays.pick_state !== facetScope || !dayGroups ||
        dayGroups.pick_state !== facetScope || dayGroups.day !== facetDay) return;
    let disposed = false;
    let checking = false;
    const daySignature = (items: ObservedDayBucket[]) =>
      items.map(item => item.day + ":" + item.count + ":" + item.pickedCount).join("|");
    const groupSignature = (item: DayGroupsSummary) =>
      item.day + ":" + item.total + "|" +
      item.items.map(group => group.key + ":" + group.count).join("|");
    const baselineDays = daySignature(observedDays.items);
    const baselineGroups = groupSignature(dayGroups);
    const poll = async () => {
      if (disposed || checking || document.visibilityState === "hidden") return;
      checking = true;
      try {
        const [latestDays, latestGroups] = await Promise.all([
          spyFetch<ObservedDaysSummary>("/listings/observed-days?pick_state=" + facetScope),
          spyFetch<DayGroupsSummary>("/listings/observed-day-groups?day=" +
            encodeURIComponent(facetDay) + "&pick_state=" + facetScope),
        ]);
        if (!disposed && (daySignature(latestDays.items) !== baselineDays ||
          groupSignature(latestGroups) !== baselineGroups)) {
          setRefresh(value => value + 1);
        }
      } catch {
        // Transient Scout/backend outages must not interrupt the active table
        // or discard its last good counts; the next tick will retry.
      } finally {
        checking = false;
      }
    };
    const interval = window.setInterval(poll, 60_000);
    window.addEventListener("focus", poll);
    return () => {
      disposed = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", poll);
    };
  }, [observedDays, dayGroups, facetDay, facetScope, showProductFacets]);
  useEffect(() => {
    // Keep Today in Vietnam synchronized if the dashboard stays open overnight.
    const interval = window.setInterval(() => {
      const next = vietnamCalendarDay();
      if (next === clockDay) return;
      setOptions(current => current.observedDay === clockDay
        ? { ...current, observedDay: next, page: 1 } : current);
      setClockDay(next);
      setRefresh(current => current + 1);
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [clockDay]);
  useEffect(() => {
    // Keyword Tree loads all source pages in its own panel, not a single paged slice.
    if (options.tab === "keywords" ||
      (options.tab === "products" && (options.blacklistView || options.winKeywordsView))) return;
    let alive = true;
    setFetching(true); setError("");
    const endpoint = { products: "/listings", keywords: "/keywords",
      trademarks: "/trademarks", logs: "/logs" }[options.tab];
    spyFetch<Page<Listing | Keyword | Trademark | SpyLog>>(endpoint + "?" + apiQuery(options).toString())
      .then(value => { if (alive) {
        setTableResult({ requestKey, data: value });
        setFetching(false);
      } })
      .catch(e => { if (alive) {
        setTableResult({ requestKey, data: null });
        setError(e instanceof Error ? e.message : "Request failed");
        setFetching(false);
      } });
    return () => { alive = false; };
  }, [requestKey]);
  const addBlacklist = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canManage || blacklistBusy) return;
    const phrases = blacklistInput.split(/[;,\n]+/).map(x => x.trim()).filter(Boolean);
    if (!phrases.length) return;
    setBlacklistBusy(true); setBlacklistError("");
    try {
      const result = await spyFetch<{ phrases: string[] }>("/blacklist", {
        method: "POST", body: JSON.stringify({ phrases }),
      });
      setBlacklist(result.phrases); setBlacklistInput("");
      selectTab("products", { blacklistView: true });
      setRefresh(n => n + 1);
    } catch(e) {
      setBlacklistError(e instanceof Error ? e.message : "Cannot save blacklist");
      selectTab("products", { blacklistView: true });
    } finally {setBlacklistBusy(false);}
  };
  const removeBlacklist = async (phrase: string) => {
    if (!canManage || blacklistBusy) return;
    setBlacklistBusy(true); setBlacklistError("");
    try {
      const result = await spyFetch<{ phrases: string[] }>(
        "/blacklist/" + encodeURIComponent(phrase), { method: "DELETE" });
      setBlacklist(result.phrases); update({ page: 1 }); setRefresh(n => n + 1);
    } catch(e) {
      setBlacklistError(e instanceof Error ? e.message : "Cannot remove phrase");
    } finally {setBlacklistBusy(false);}
  };
  const addWinKeywords = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canManage || winKeywordBusy) return;
    const keywords = winKeywordInput.split(/[;,\n]+/).map(value => value.trim()).filter(Boolean);
    if (!keywords.length) return;
    setWinKeywordBusy(true); setWinKeywordError("");
    try {
      const response = await spyFetch<{ keywords: string[] }>("/win-keywords", {
        method: "POST", body: JSON.stringify({ keywords }),
      });
      setWinKeywords(response.keywords); setWinKeywordInput("");
    } catch (err) {
      setWinKeywordError(err instanceof Error ? err.message : "Cannot save Win Keyword");
    } finally { setWinKeywordBusy(false); }
  };
  const removeWinKeyword = async (keyword: string) => {
    if (!canManage || winKeywordBusy) return;
    setWinKeywordBusy(true); setWinKeywordError("");
    try {
      const response = await spyFetch<{ keywords: string[] }>("/win-keywords", {
        method: "DELETE", body: JSON.stringify({ keyword }),
      });
      setWinKeywords(response.keywords);
    } catch (err) {
      setWinKeywordError(err instanceof Error ? err.message : "Cannot remove Win Keyword");
    } finally { setWinKeywordBusy(false); }
  };
  const queuePreviouslyPicked = async () => {
    if (!canManage || extractionBackfillBusy || !extraction?.enabled) return;
    setExtractionBackfillBusy(true);
    setExtractionBackfillMessage("");
    try {
      const batchSize = extraction.daily_cap === 1 ? 1 : Math.min(20, extraction.daily_cap);
      const result = await spyFetch<{
        created: number; ready: number; blocked?: string;
        skipped_no_source: number;
      }>("/extractions/backfill", {
        method: "POST", body: JSON.stringify({ limit: batchSize, dry_run: false }),
      });
      setExtractionBackfillMessage(result.blocked === "pilot_job_pending"
        ? "Image Agent pilot đang xử lý job trước đó."
        : "Đã đưa " + result.created + " design vào hàng đợi · " +
          result.skipped_no_source + " ảnh thiếu nguồn hợp lệ. PNG cần QA và xác minh quyền.");
      setRefresh(previous => previous + 1);
    } catch (err) {
      setExtractionBackfillMessage(err instanceof Error ? err.message : "Cannot queue Picked designs");
    } finally {
      setExtractionBackfillBusy(false);
    }
  };
  const togglePicked = async (item: Listing) => {
    if (!canManage || pickBusyKey !== null) return;
    setPickBusyKey(item.listingKey);
    setPickError("");
    try {
      await spyFetch<{ listingKey: string; picked: boolean }>(
        "/listings/" + encodeURIComponent(item.listingKey) + "/pick", {
          method: "PUT", body: JSON.stringify({ picked: !item.picked }),
        });
      // Refetch the correct bucket, counters and current page atomically.
      // Only the user decision moves a product; a Scout re-sync never resets it.
      update({ page: 1 });
      setRefresh(n => n + 1);
    } catch (e) {
      setPickError(e instanceof Error ? e.message : "Cannot change Pick state");
    } finally {
      setPickBusyKey(null);
    }
  };
  const toggleLater = async (item: Listing) => {
    if (!canManage || laterBusyKey !== null || pickBusyKey !== null || item.picked) return;
    setLaterBusyKey(item.listingKey);
    setLaterError("");
    try {
      await spyFetch<{ listingKey: string; later: boolean }>(
        "/listings/" + encodeURIComponent(item.listingKey) + "/later", {
          method: "PUT", body: JSON.stringify({ later: !item.later }),
        });
      update({ page: 1 });
      setRefresh(n => n + 1);
    } catch (e) {
      setLaterError(e instanceof Error ? e.message : "Cannot update Later");
    } finally {
      setLaterBusyKey(null);
    }
  };
  const columns = useMemo(() => {
    if (options.tab === "products") return SORTS.products;
    if (options.tab === "keywords") return SORTS.keywords;
    if (options.tab === "trademarks") return SORTS.trademarks;
    return SORTS.logs;
  }, [options.tab]);
  const syncLabel = summary?.source_status === "online" ? "Agent online"
    : summary?.source_status === "sync_delayed" ? "Sync delayed"
    : summary?.source_status === "never_synced" ? "Never synced" : "Agent offline";
  const totalPages = Math.max(1, rows?.pages || 1);
  const sortColumn = (id: string) => update({
    sort: id, dir: options.sort === id && options.dir === "desc" ? "asc" : "desc",
  });
  const activeTab = options.tab;
  const isBlacklistView = activeTab === "products" && options.blacklistView;
  const isWinKeywordsView = activeTab === "products" && options.winKeywordsView;
  const dailyCards = recentObservedDays(clockDay).map((day, index) => {
    const bucket = observedDays?.pick_state === facetScope
      ? observedDays.items.find(item => item.day === day) : undefined;
    return {
      day, index, count: bucket?.count ?? null,
      pickedCount: bucket?.pickedCount ?? null,
    };
  });
  const filteredBlacklist = blacklist.filter(phrase =>
    phrase.toLowerCase().includes(typedSearch.trim().toLowerCase()));
  const filteredWinKeywords = winKeywords.filter(keyword =>
    keyword.toLocaleLowerCase().includes(typedSearch.trim().toLocaleLowerCase()));
  const selectTab = (tab: SpyTab, filters: Partial<Options> = {}) => {
    setTypedSearch("");
    update({
      tab, q: "", sessionId: "", source: "", badge: "", tm: "", group: "",
      status: "", level: "", phase: "", pickState: "eligible",
      blacklistView: false, winKeywordsView: false,
      observedDay: clockDay,
      sort: tab === "products" ? "observedAt" : tab === "logs" ? "at"
        : tab === "trademarks" ? "checkedAt" : "updatedAt",
      dir: "desc", page: 1, ...filters,
    });
  };
  const overviewCards: {
    title: string; value: number | string | null | undefined; hint: string;
    tone: string; tab: SpyTab; filters?: Partial<Options>; selected: boolean;
  }[] = [
    { title: "Eligible listings", value: summary?.listings, hint: "Ready to review",
      tone: "total", tab: "products", filters: { observedDay: clockDay },
      selected: activeTab === "products" && !isBlacklistView && !isWinKeywordsView &&
        options.pickState === "eligible" && !options.source && !options.tm },
    { title: "Picked", value: summary?.picked ?? 0,
      hint: extraction ? (extraction.enabled
        ? extraction.storage?.ready === false ? "Selected · Extraction storage paused"
          : `Extract ${extraction.claimed_today}/${extraction.daily_cap} · Q ${extraction.queued} · PNG ${extraction.needs_review_today} · AI ${extraction.needs_ai_today}`
        : "Selected · Image Agent off")
        : "Manually selected products",
      tone: "picked", tab: "products", filters: { pickState: "picked", observedDay: "all" },
      selected: activeTab === "products" && !isBlacklistView && !isWinKeywordsView &&
        options.pickState === "picked" },
    { title: "Later", value: summary?.later ?? 0, hint: "Auto-grouped design themes",
      tone: "later", tab: "products", filters: { pickState: "later", observedDay: "all" },
      selected: activeTab === "products" && !isBlacklistView && !isWinKeywordsView &&
        options.pickState === "later" },
    { title: "Blacklist", value: blacklist.length, hint: "Blocked keyword phrases",
      tone: "blacklist", tab: "products", filters: { blacklistView: true },
      selected: isBlacklistView },
    { title: "Win Keyword", value: winKeywords.length, hint: "Saved keyword notes",
      tone: "win-keyword", tab: "products", filters: { winKeywordsView: true },
      selected: isWinKeywordsView },
    { title: "Keyword nodes", value: summary?.keywords, hint: "Discovery branches",
      tone: "keywords", tab: "keywords", selected: activeTab === "keywords" },
  ];
  return <main className="etsy-spy-page">
    <WorkspacePageHeader route="etsy-spy" actions={<>
      <span className={"etsy-connection " + (summary?.source_status || "never_synced")}>{syncLabel}</span>
      <button type="button" className="etsy-btn etsy-refresh" onClick={() => setRefresh(n => n + 1)}><span aria-hidden="true">↻</span> Refresh</button>
      <WorkspaceBackToAssets />
    </>} />
    <div className="etsy-stage-nav">
      <nav role="tablist" aria-label="Etsy Spy sections" className="etsy-tabs">
        {TABS.map((item, index) => <button key={item.id} type="button" role="tab"
          id={"etsy-tab-" + item.id} aria-controls="etsy-spy-tabpanel"
          tabIndex={activeTab === item.id ? 0 : -1}
          aria-selected={activeTab === item.id} className={activeTab === item.id ? "active" : ""}
          onClick={() => selectTab(item.id)}
          onKeyDown={event => {
            const next = event.key === "ArrowRight" ? (index + 1) % TABS.length
              : event.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length
              : event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : -1;
            if (next < 0) return;
            event.preventDefault();
            selectTab(TABS[next].id);
            const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
            tabs?.[next]?.focus();
          }}>
          <span className="etsy-tab-number" aria-hidden="true">{index + 1}</span>
          <span className="etsy-tab-copy"><strong>{item.title}</strong><small>{item.subtitle}</small></span>
        </button>)}
      </nav>
    </div>
    <section id="etsy-spy-tabpanel" role="tabpanel"
      aria-labelledby={"etsy-tab-" + activeTab} className="etsy-stage-canvas" aria-label="Etsy Spy workspace">
      <div className="etsy-stage-card">
        <div className="etsy-stage-heading-row">
          <div className="etsy-stage-heading">
            <span className="etsy-stage-kicker">ETSY SPY · MARKET INTELLIGENCE</span>
            <h2>{activeTab === "products" ? "Product Intelligence" : TABS.find(item => item.id === activeTab)?.title}</h2>
            <p>{activeTab === "products"
              ? "Explore verified Etsy product demand and trademark evidence from the Windows Scout."
              : activeTab === "keywords" ? "Follow discovered keyword branches, priorities and source evidence."
              : activeTab === "trademarks" ? "Review cached trademark results and attached USPTO evidence."
              : "Monitor Windows Scout synchronization, warnings and processing errors."}</p>
          </div>
          <div className="etsy-stage-heading-actions">
            {activeTab === "products" && !isWinKeywordsView && <form className="etsy-blacklist-form"
              onSubmit={addBlacklist} aria-label="Blacklist product title phrases">
              <span className="etsy-blacklist-icon" aria-hidden="true">⊘</span>
              <input type="text" aria-label="Add blacklist phrases" value={blacklistInput}
                onChange={event => setBlacklistInput(event.target.value)}
                placeholder={canManage ? "Blacklist keywords · Enter" : "Blacklist (read only)"}
                title="Enter to blacklist phrases; separate multiple phrases with commas"
                disabled={!canManage || blacklistBusy} maxLength={400}/>
            </form>}
            {isWinKeywordsView && <form className="etsy-blacklist-form etsy-win-keyword-form"
              onSubmit={addWinKeywords} aria-label="Win Keyword list">
              <span className="etsy-blacklist-icon" aria-hidden="true">☆</span>
              <input type="text" aria-label="Add Win Keyword" value={winKeywordInput}
                onChange={event => setWinKeywordInput(event.target.value)}
                placeholder={canManage ? "Win Keyword · Enter" : "Win Keyword (read only)"}
                title="Press Enter to save phrases; separate multiple phrases with commas"
                disabled={!canManage || winKeywordBusy} maxLength={400} />
            </form>}
            <div className="etsy-search-box">
              <svg className="etsy-search-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
              <input type="search" aria-label="Search Spy records"
                placeholder={isBlacklistView ? "Search blocked phrases…" :
                  isWinKeywordsView ? "Search saved Win Keywords…" :
                  activeTab === "logs" ? "Search message or stack…" : "Search keyword or product…"}
                value={typedSearch} onChange={event => setTypedSearch(event.target.value)} />
            </div>
          </div>
        </div>
        <section className="etsy-stats" aria-label="Etsy Spy overview filters">
          {overviewCards.map((card, index) => <button type="button"
            className={`etsy-stat etsy-stat-${card.tone}${card.selected ? " active" : ""}`}
            key={card.title} aria-pressed={card.selected}
            title={`View ${card.title.toLowerCase()} records`}
            onClick={() => selectTab(card.tab, card.filters)}>
            <span className="etsy-stat-heading">
              <span className="etsy-stat-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={SUMMARY_ICONS[index] || SUMMARY_ICONS[0]} /></svg></span>
              <span>{card.title}</span>
            </span>
            <strong>{typeof card.value === "number" ? card.value.toLocaleString() : card.value ?? "—"}</strong>
            <small>{card.hint}</small>
            {card.title === "Picked" && summary?.picked_images &&
              <span className="etsy-picked-image-status" aria-label="Picked listing image URL completeness">
                URL ảnh: {summary.picked_images.missing.toLocaleString("vi-VN")} thiếu · {summary.picked_images.complete.toLocaleString("vi-VN")} đủ
              </span>}
          </button>)}
        </section>
        {showProductFacets && <>
          <section className="etsy-observed-days"
            aria-label={"Phân loại " + facetScope + " theo ngày, 06:00 đến 06:00 giờ Việt Nam"}>
            <div className="etsy-observed-day-grid">
              {dailyCards.map(({ day, index, count, pickedCount }) => {
                const label = index === 0 ? "Hôm nay" : index === 1 ? "Hôm qua"
                  : day.slice(8, 10) + "/" + day.slice(5, 7);
                const selected = options.observedDay === day;
                const statusLabel = facetScope === "eligible" ? "Khả dụng"
                  : facetScope === "picked" ? "Picked" : "Later";
                const dateBasis = facetScope === "picked" ? "ngày Pick"
                  : facetScope === "later" ? "ngày phân loại Later" : "ngày quan sát";
                const dateRange = "06:00 ngày " + day + " đến trước 06:00 ngày tiếp theo (GMT+7)";
                return <button type="button" key={day}
                  className={"etsy-observed-day" + (selected ? " active" : "")}
                  data-observed-day={day} data-pick-state={facetScope} aria-pressed={selected}
                  aria-label={label + ": " + (count ?? "Chưa tải") + " " + statusLabel +
                    (facetScope === "eligible" ? ", " + (pickedCount ?? "Chưa tải") + " sản phẩm đã Pick" : "") +
                    "; " + dateRange + ". Nhấn để lọc theo " + dateBasis}
                  title={"Ngày " + day + " · " + dateBasis + " · " + dateRange}
                  onClick={() => update({ observedDay: selected ? "all" : day, group: "" })}>
                  <span className="etsy-observed-day-label">{label}</span>
                  <span className="etsy-observed-day-metrics">
                    <span className="etsy-observed-day-eligible">
                      <strong>{count === null ? "—" : count.toLocaleString("vi-VN")}</strong>
                      <small>{statusLabel}</small>
                    </span>
                    {facetScope === "eligible" && <span className={"etsy-observed-day-pick" + (pickedCount ? " has-picks" : "")}>
                      <strong>{pickedCount === null ? "—" : pickedCount.toLocaleString("vi-VN")}</strong>
                      <small>Picked</small>
                    </span>}
                  </span>
                </button>;
              })}
            </div>
          </section>
          <section className="etsy-day-groups" aria-label={"Nhóm " + facetScope + " ngày " + (facetDay ?? "tất cả")}>
            <span className="etsy-day-groups-title">Nhóm · {
              !facetDay ? "Tất cả ngày" :
              facetDay === clockDay ? "Hôm nay" :
              facetDay === recentObservedDays(clockDay)[1] ? "Hôm qua" :
              facetDay.slice(8, 10) + "/" + facetDay.slice(5, 7)
            }</span>
            {!facetDay ? <span className="etsy-day-groups-empty">Chọn một ngày phía trên để xem nhóm {facetScope}.</span>
              : dayGroups?.day === facetDay && dayGroups.pick_state === facetScope ? (
              dayGroups.total > 0 ? <div className="etsy-day-groups-list">
                {[{key: "", label: "Tất cả", count: dayGroups.total}, ...dayGroups.items]
                  .map(item => {
                    const pressed = options.observedDay === facetDay && options.group === item.key;
                    return <button type="button" key={item.key || "all"}
                      className={"etsy-day-group" + (pressed ? " active" : "")}
                      aria-pressed={pressed}
                      title={"Lọc " + facetScope + " · " + item.label + " trong ngày " + facetDay}
                      onClick={() => update({
                        group: item.key === options.group && pressed ? "" : item.key,
                      })}>
                      <span>{item.label}</span><strong>{item.count.toLocaleString("vi-VN")}</strong>
                    </button>;
                  })}
              </div> : <span className="etsy-day-groups-empty">Không có sản phẩm {facetScope} trong ngày này</span>
            ) : <span className="etsy-day-groups-empty">
              {dayGroupError ? "Chưa tải được nhóm" : "Đang tải nhóm…"}
            </span>}
          </section>
        </>}
        {activeTab === "products" && !showProductFacets && <div className="etsy-facet-context">
          {isBlacklistView
            ? "Blacklist · quản lý cụm từ bị chặn, không sử dụng phân nhóm sản phẩm theo ngày."
            : "Win Keyword · danh sách ghi chú keyword, không sử dụng phân nhóm sản phẩm theo ngày."}
        </div>}
        {isBlacklistView ? <section className="etsy-blacklist-panel"
          aria-label="Blacklist keyword management" aria-labelledby="etsy-blacklist-heading">
          <div className="etsy-blacklist-panel-header">
            <div>
              <h2 id="etsy-blacklist-heading">Blacklist <span>({blacklist.length})</span></h2>
              <p>Products containing these phrases are excluded from Eligible listings and future Scout searches.
                Add phrases using the Blacklist field beside Search, then press Enter.</p>
            </div>
            <span className="etsy-blacklist-panel-counter">{blacklist.length} blocked</span>
          </div>
          {blacklistError && <p className="etsy-blacklist-error" role="alert">{blacklistError}</p>}
          {filteredBlacklist.length > 0 ?
            <div className="etsy-blacklist-chips" aria-label="Active blacklist phrases">
              {filteredBlacklist.map(phrase => <span className="etsy-blacklist-chip" key={phrase}>
                {phrase}{canManage && <button type="button"
                  aria-label={"Remove blacklist " + phrase}
                  disabled={blacklistBusy} onClick={() => removeBlacklist(phrase)}>×</button>}
              </span>)}
            </div> :
            <p className="etsy-blacklist-empty">{blacklist.length
              ? "No blocked phrases match your search."
              : "Blacklist is empty. Enter a keyword phrase above and press Enter."}</p>}
        </section> : isWinKeywordsView ? <section className="etsy-blacklist-panel etsy-win-keyword-panel"
          aria-label="Win Keyword management" aria-labelledby="etsy-win-keyword-heading">
          <div className="etsy-blacklist-panel-header">
            <div>
              <h2 id="etsy-win-keyword-heading">Win Keyword <span>({winKeywords.length})</span></h2>
              <p>Save keywords for reference. These entries do not affect Scout, product filtering or trademark checks.
                Add keywords using the Win Keyword field above and press Enter.</p>
            </div>
            <span className="etsy-blacklist-panel-counter">{winKeywords.length} saved</span>
          </div>
          {winKeywordError && <p className="etsy-blacklist-error" role="alert">{winKeywordError}</p>}
          {filteredWinKeywords.length > 0 ?
            <div className="etsy-blacklist-chips" aria-label="Saved Win Keywords">
              {filteredWinKeywords.map(keyword => <span className="etsy-blacklist-chip" key={keyword}>
                {keyword}{canManage && <button type="button"
                  aria-label={"Remove Win Keyword " + keyword}
                  disabled={winKeywordBusy} onClick={() => removeWinKeyword(keyword)}>×</button>}
              </span>)}
            </div> :
            <p className="etsy-blacklist-empty">{winKeywords.length
              ? "No Win Keywords match your search."
              : "No Win Keywords yet. Enter a keyword phrase above and press Enter."}</p>}
        </section> : <>
    {activeTab === "trademarks" && <section className="etsy-lookup">
      <div><strong>Cached trademark lookup</strong>
        <small>Exact / partial · 1–30 lines · evidence from Windows. No live USPTO/TMHunt request.</small>
      </div>
      <textarea aria-label="Trademark queries" rows={2} placeholder={"Example phrase 1\nExample phrase 2"}
        value={tmText} onChange={event => setTmText(event.target.value)} />
      <div className="etsy-lookup-actions">
        <select value={tmMode} onChange={event => setTmMode(event.target.value as typeof tmMode)}>
          <option value="exactMatch">Exact</option><option value="partialMatch">Partial</option>
        </select>
        <button className="etsy-btn etsy-primary" onClick={async () => {
          const queries = tmText.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
          setTmLookupError(""); setTmResult(null);
          try {
            const result = await spyFetch<{ matched: Mark[] }>("/trademarks/lookup", {
              method: "POST", body: JSON.stringify({ queries, mode: tmMode }),
            });
            setTmResult(safeMarks(result.matched));
          } catch (e) { setTmLookupError(e instanceof Error ? e.message : "Lookup failed"); }
        }}>Lookup cache</button>
      </div>
      {tmLookupError && <p role="alert" className="etsy-error">{tmLookupError}</p>}
      {tmResult && <div className="etsy-lookup-results"><strong>{tmResult.length} cached matches</strong>
        <EvidenceMarks matches={tmResult} /></div>}
      <small>LIVE is a potential warning. DEAD or no match never proves trademark clearance. Verify at <a
        target="_blank" rel="noopener noreferrer" href="https://tsdr.uspto.gov/">USPTO TSDR</a>.</small>
    </section>}
    {activeTab === "products" && options.pickState === "picked" &&
      !isBlacklistView && !isWinKeywordsView && <section className="etsy-extraction-backfill"
        aria-label="Picked design extraction queue">
        <div>
          <strong>Picked → Transparent PNG</strong>
          <small>Fast Extraction trước · AI review khi ảnh khó · mọi kết quả cần QA và xác minh quyền.</small>
        </div>
        <button type="button" disabled={!canManage || !extraction?.enabled ||
          extractionBackfillBusy || extraction.storage?.ready !== true}
          onClick={queuePreviouslyPicked}>
          {extractionBackfillBusy ? "Đang tạo hàng đợi…" :
            extraction?.daily_cap === 1 ? "Queue 1 Picked (pilot)" : "Queue Picked designs"}
        </button>
        {extractionBackfillMessage && <span role="status">{extractionBackfillMessage}</span>}
      </section>}
    {pickError && <div role="alert" className="etsy-error">Cannot update Pick: {pickError}</div>}
    {laterError && <div role="alert" className="etsy-error">Cannot update Later: {laterError}</div>}
    {activeTab === "products" && options.pickState === "later" && !isBlacklistView && !isWinKeywordsView &&
      <p className="etsy-later-help">Later groups similar design themes from your saved examples.
        Automatically matched products include a reason. Remove a seed to update its group,
        or dismiss one suggestion without affecting Scout.</p>}
    {error && <div role="alert" className="etsy-error">Cannot load Etsy Spy data: {error}
      <button onClick={() => setRefresh(n => n + 1)}>Retry</button></div>}
    {activeTab !== "products" && <div className="etsy-table-section-header">
      <h2>{TABS.find(x => x.id === activeTab)?.title} <span>({activeTab === "keywords"
        ? (options.sessionId ? "Session" : summary?.keywords?.toLocaleString() ?? "—")
        : rows ? rows.total.toLocaleString() : "—"})</span></h2>
      <span className="etsy-table-count">{activeTab === "keywords"
        ? "Interactive Mindmap · grouped keywords · original Scout history preserved"
        : "Sorted and filtered from synchronized Windows data"}</span>
    </div>}
    {activeTab === "keywords" ?
      <KeywordTreeView sessionId={options.sessionId} search={options.q} status={options.status}
        sort={options.sort} direction={options.dir} refresh={refresh}
        canManage={canManage} onSort={sortColumn} />
      : <div className="etsy-table-card">
      {loading ? <div role="status" className="etsy-skeleton">Loading synchronized records…</div>
        : !rows?.items.length ? <div className="etsy-empty">
          {activeTab === "products" && options.pickState === "later"
            ? "No Later products yet. Use the Later button beside Open on Etsy to group related design themes."
            : <>No matching records. {summary?.source_status === "never_synced"
                ? "Pair the Windows agent to begin the initial historical sync." : "Try adjusting filters."}</>}
        </div>
        : <div className="etsy-table-wrap">
          <table className={"etsy-data-table is-" + activeTab}><thead><tr>
            {columns.map(col => <th key={col.id}
              aria-sort={options.sort === col.id ? (options.dir === "asc" ? "ascending" : "descending") : undefined}>
              {col.id === "keywordSms" ? <span className="etsy-static-header" title="Search Match Suggestions">
                {col.text}
              </span> : <button type="button" onClick={() => sortColumn(col.id)}
                aria-label={"Sort by " + col.text}>
                {col.text}{options.sort === col.id ? (options.dir === "asc" ? " ↑" : " ↓") : ""}
              </button>}
            </th>)}
            {activeTab === "logs" && <th>Message / Stack</th>}
            {activeTab === "products" && <th className="etsy-action-header">Action</th>}
          </tr></thead><tbody>
            {activeTab === "products" ? (rows.items as Listing[]).map(item =>
              <ProductRow key={item.sessionId + "/" + item.keyword + "/" + item.sourceKind + "/" + item.listingKey}
                item={item} canPick={canManage}
                busy={pickBusyKey !== null || laterBusyKey !== null}
                onPick={togglePicked} onLater={toggleLater} />)
              : activeTab === "trademarks" ? (rows.items as Trademark[]).map(item =>
                <TrademarkRow key={item.checkId} item={item} />)
              : (rows.items as SpyLog[]).map(item => <LogRow key={item.eventId} item={item} />)}
          </tbody></table>
        </div>}
      <div className="etsy-pager">
        <span>{rows && rows.total > 0 ? ((options.page - 1) * options.pageSize + 1).toLocaleString() : 0}–{rows ? Math.min(options.page * options.pageSize, rows.total).toLocaleString() : "—"} of {rows?.total.toLocaleString() ?? "—"}</span>
        <div className="etsy-page-controls">
          <button type="button" aria-label="First page" disabled={loading || options.page <= 1} onClick={() => update({ page: 1 })}>«</button>
          <button type="button" aria-label="Previous page" disabled={loading || options.page <= 1} onClick={() => update({ page: options.page - 1 })}>‹</button>
          <strong>Page {options.page} / {totalPages}</strong>
          <button type="button" aria-label="Next page" disabled={loading || options.page >= totalPages} onClick={() => update({ page: options.page + 1 })}>›</button>
          <button type="button" aria-label="Last page" disabled={loading || options.page >= totalPages} onClick={() => update({ page: totalPages })}>»</button>
        </div>
        <label>Rows
          <select aria-label="Rows per page" value={options.pageSize} disabled={loading}
            onChange={event => update({ pageSize: Number(event.target.value) })}>
            {[50, 100, 200].map(n => <option value={n} key={n}>{n}</option>)}
          </select>
        </label>
      </div>
    </div>}
        </>}
    <details className="etsy-agents" aria-label="Windows synchronization agents">
      <summary><span>Windows Sync Agent</span><small>{agents.length} registered · Data remains available while the PC is offline</small></summary>
      <div className="etsy-agent-content">
      <div className="etsy-agent-rows">
        {agents.map(agent => <div key={agent.id}>
          <strong>{agent.name}</strong><span>{agent.active ? "Active" : "Revoked"}</span>
          <small>{agent.machine_label || "Unreported"} · API {agent.local_api_version || "—"} ·
            Seen {readableTime(agent.last_seen_at)} · Sync {readableTime(agent.last_synced_at)}</small>
        </div>)}
        {!agents.length && <span className="etsy-muted">No Windows agent paired yet.</span>}
      </div>
      {canManage && <div className="etsy-pair">
        <button onClick={() => setPairOpen(o => !o)} className="etsy-btn">Pair Windows agent</button>
        {pairOpen && <>
          <div className="etsy-pair-line"><input aria-label="Agent name" value={pairName} onChange={e => setPairName(e.target.value)} />
            <button disabled={pairBusy} className="etsy-btn etsy-primary" onClick={async () => {
              setPairBusy(true); setPairError(""); setPairing(null);
              try {
                const response = await spyFetch<{ agent_id: string; token: string }>("/agents", {
                  method: "POST", body: JSON.stringify({ name: pairName }),
                });
                setPairing(response); setRefresh(n => n + 1);
              } catch (e) { setPairError(e instanceof Error ? e.message : "Pairing failed"); }
              finally { setPairBusy(false); }
            }}>Create agent</button>
          </div>
          {pairError && <p role="alert" className="etsy-error">{pairError}</p>}
          {pairing && <div className="etsy-pair-secret">
            <strong>Copy once into Windows sync config. This secret will not be shown again.</strong>
            <code>Agent ID: {pairing.agent_id}</code>
            <code>Token: {pairing.token}</code>
            <button className="etsy-btn" onClick={() => void navigator.clipboard?.writeText(
              "CAM_ETSY_SYNC_AGENT_ID=" + pairing.agent_id + "\nCAM_ETSY_SYNC_TOKEN=" + pairing.token,
            )}>Copy credentials</button>
            <small>Store them only on DESKTOP-3FR0LF0, never in Git or browser localStorage.</small>
          </div>}
        </>}
      </div>}
      </div>
    </details>
    <details className="etsy-agents" aria-label="Windows image extraction agents">
      <summary><span>Windows Image Agent</span>
        <small>{imageAgents.filter(agent => agent.active).length} paired · {extraction?.enabled
          ? `Fast Extraction ${extraction.claimed_today}/${extraction.daily_cap} claims today`
          : "Extraction disabled until pilot is approved"}</small>
      </summary>
      <div className="etsy-agent-content">
        <p className="etsy-muted" role="status">
          Storage: {extraction?.storage
            ? extraction.storage.ready
              ? `Ready · ${Math.round(extraction.storage.used_bytes / 1048576)} MB / ${Math.round(extraction.storage.quota_bytes / 1048576)} MB`
              : `Paused · ${extraction.storage.reason.replaceAll("_", " ")}`
            : "Not configured"}
          {" · "}Daily cap: {extraction?.daily_cap ?? "—"} new claims (GMT+7)
        </p>
        <div className="etsy-agent-rows">
          {imageAgents.map(agent => <div key={agent.id}>
            <strong>{agent.name}</strong><span>{agent.active ? "Paired" : "Revoked"}</span>
            <small>Seen {readableTime(agent.last_seen_at)}</small>
          </div>)}
          {!imageAgents.length && <span className="etsy-muted">No dedicated Image Agent paired yet.</span>}
        </div>
        {canManage && <div className="etsy-pair">
          <button type="button" className="etsy-btn" onClick={() => {
            setImagePairOpen(current => !current);
            setImagePairSecret(null); setImagePairError("");
          }}>Pair Image Agent</button>
          {imagePairOpen && <>
            <div className="etsy-pair-line">
              <input aria-label="Image Agent name" value={imagePairName}
                maxLength={64} onChange={event => setImagePairName(event.target.value)} />
              <button type="button" className="etsy-btn etsy-primary"
                disabled={imagePairBusy || !imagePairName.trim()} onClick={async () => {
                  setImagePairBusy(true); setImagePairError(""); setImagePairSecret(null);
                  try {
                    const paired = await spyFetch<{ agent_id: string; token: string }>("/image-agents", {
                      method: "POST", body: JSON.stringify({ name: imagePairName.trim() }),
                    });
                    setImagePairSecret(paired);
                    setRefresh(value => value + 1);
                  } catch (error) {
                    setImagePairError(error instanceof Error ? error.message : "Pair Image Agent failed");
                  } finally {
                    setImagePairBusy(false);
                  }
                }}>Create Image Agent</button>
            </div>
            {imagePairError && <p className="etsy-error" role="alert">{imagePairError}</p>}
            {imagePairSecret && <div className="etsy-pair-secret">
              <strong>Credentials appear once. Copy to the Windows D: configuration, not Git.</strong>
              <code>Agent ID: {imagePairSecret.agent_id}</code>
              <code>Token: {imagePairSecret.token}</code>
              <button type="button" className="etsy-btn" onClick={() => {
                const config = {
                  enabled: false,
                  cam_origin: window.location.origin,
                  agent_id: imagePairSecret.agent_id,
                  tenant_id: "boostgame",
                  token: imagePairSecret.token,
                };
                void navigator.clipboard?.writeText(JSON.stringify(config, null, 2));
              }}>Copy disabled Windows config</button>
              <small>Save to D:\CAM Image Extension\.runtime\extraction\cam-config.json.
                The relay remains OFF until the storage and one-listing pilot are verified.</small>
              <button type="button" className="etsy-btn"
                onClick={() => setImagePairSecret(null)}>Hide token</button>
            </div>}
          </>}
        </div>}
      </div>
    </details>
      </div>
    </section>
  </main>;
}