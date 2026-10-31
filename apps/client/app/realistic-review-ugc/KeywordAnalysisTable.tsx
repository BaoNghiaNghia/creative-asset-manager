import { useEffect, useId, useState } from "react";
import { attachKeywordSourceImage } from "./api";
import { KeywordSearchInput } from "./KeywordSearchInput";
import { formatRelativeDateTime } from "./RelativeDateTime";
import { SortDirectionIndicator } from "./SortDirectionIndicator";
import "./SortColumnHighlight.css";
import { DeferredImage } from "./DeferredImage";
import { RrugcStageHeader } from "./RrugcStageHeader";
import "./KeywordAnalysisPlatform.css";
import "./KeywordAgentColumn.css";
import type {
  KeywordAnalysisSortBy,
  KeywordAnalysisSortDirection,
  KeywordTailFilter,
  KeywordUsageFilter,
  ScoutFeedbackAction,
  ScoutFeedbackScope,
} from "./api";
import type { KeywordVolume, KeywordVolumePage, KeywordVolumeTrendPoint } from "./types";

const PAGE_SIZE_OPTIONS = [20, 50, 100, 500] as const;
// Scout feedback controls are scoped to a keyword and its source Pin.

type KeywordTailKind = Exclude<KeywordTailFilter, "all">;

function KeywordTailIcon({ kind }: { kind: KeywordTailKind }) {
  const tokens = kind === "short"
    ? [[2, 8, 8, 7], [12, 8, 13, 7]]
    : kind === "mid"
      ? [[2, 4, 8, 6], [12, 4, 13, 6], [2, 14, 11, 6], [15, 14, 9, 6]]
      : [[2, 2, 8, 5], [12, 2, 13, 5], [2, 9.5, 11, 5], [15, 9.5, 9, 5], [2, 17, 6, 5], [10, 17, 14, 5]];
  return <svg viewBox="0 0 27 24" aria-hidden="true" focusable="false">
    {tokens.map(([x, y, width, height], index) => (
      <rect key={index} x={x} y={y} width={width} height={height} rx={height / 2} fill="currentColor" opacity={1 - index * .08} />
    ))}
  </svg>;
}

function formatCpc(value: number | null): string {
  return typeof value === "number" ? "$" + value.toFixed(2) : "—";
}

function formatChange(value: number | null | undefined): string {
  if (typeof value !== "number") return "—";
  return (value > 0 ? "+" : "") + value.toFixed(1) + "%";
}

export function TrademarkStatusBadge({ status }: { status: KeywordVolume["trademark_status"] }) {
  const kind = status || "unverified";
  const captions: Record<NonNullable<KeywordVolume["trademark_status"]>, string> = {
    safe: "An Toàn",
    warning: "Cảnh Báo",
    danger: "Nguy Hiểm",
    possible_match: "Cần kiểm tra",
    no_exact_match: "Không trùng chính xác",
    unverified: "Chưa xác minh",
  };
  const symbol = kind === "safe" || kind === "no_exact_match" ? "✓"
    : kind === "danger" || kind === "warning" || kind === "possible_match" ? "!" : "?";
  return <span className={"rrugc-stage0-tm-badge is-" + kind} title="AEBrowse TM screening · không thay thế việc thẩm định pháp lý">
    <span aria-hidden="true">{symbol}</span>{captions[kind]}
  </span>;
}

function competitionTone(value: string | null): string {
  return (value || "unknown").toLowerCase();
}

function changeTone(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (typeof value !== "number" || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}

function keywordTailKind(keyword: string): KeywordTailKind {
  const count = keyword.trim().split(/\s+/).filter(Boolean).length;
  return count <= 2 ? "short" : count <= 4 ? "mid" : "long";
}

function keywordTailLabel(keyword: string): string {
  const kind = keywordTailKind(keyword);
  return kind === "short" ? "Short-tail" : kind === "mid" ? "Mid-tail" : "Long-tail";
}

function formatDetailDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatTableDate(value: string | null | undefined): string {
  return formatRelativeDateTime(value);
}

function providerLabel(value: string): string {
  return value === "aebrowse_google_ads" ? "Google Ads · AEBrowse" : value === "pending" ? "AEBrowse pending" : value;
}

type AgentSource = NonNullable<NonNullable<KeywordVolume["source_agents"]>[number]["sources"]>[number];

function agentSourceLinks(item: KeywordVolume): AgentSource[] {
  const sources = item.source_agents?.flatMap(agent => agent.sources || []) || [];
  const seen = new Set<string>();
  return sources.filter(source => {
    if (!source.url || !/^https:\/\//i.test(source.url) || seen.has(source.url)) return false;
    seen.add(source.url);
    return true;
  });
}

function platformDisplay(platform: string): string {
  return platform === "etsy" ? "Etsy" : platform === "amazon" ? "Amazon"
    : platform === "pinterest" ? "Pinterest" : platform;
}

const STAGE0_PLATFORM_LOGOS: Record<string, string> = {
  etsy: "/rrugc/platforms/etsy.png",
  amazon: "/rrugc/platforms/amazon.png",
  pinterest: "/rrugc/platforms/pinterest.png",
};

function Stage0PlatformMark({ platform }: { platform: string }) {
  const logo = STAGE0_PLATFORM_LOGOS[platform];
  return logo
    ? <>
        <img className={"rrugc-stage0-platform-logo platform-logo-" + platform}
          src={logo} alt="" aria-hidden="true" loading="lazy" decoding="async" />
        {platform === "pinterest" && <span>Pinterest</span>}
      </>
    : <span>{platformDisplay(platform)}</span>;
}

function Stage0SourcePlatforms({ item }: { item: KeywordVolume }) {
  const sources = agentSourceLinks(item);
  const platforms = Array.from(new Set(
    sources.map(source => source.platform).concat(item.source_agents?.flatMap(agent => agent.platforms) || [])
      .concat(item.source_pin_url ? ["pinterest"] : []),
  )).filter(platform => platform && platform !== "unknown");
  if (!platforms.length) return <span className="rrugc-stage0-platform-empty" title="No verified source platform recorded">—</span>;
  return <div className="rrugc-stage0-platforms" aria-label={"Platforms: " + platforms.map(platformDisplay).join(", ")}>
    {platforms.map(platform => {
      const sourceUrl = sources.find(source => source.platform === platform)?.url
        || (platform === "pinterest" ? item.source_pin_url : null);
      const label = platformDisplay(platform);
      const cls = "rrugc-stage0-platform platform-" + platform;
      return sourceUrl && /^https:\/\//i.test(sourceUrl)
        ? <a key={platform} className={cls} href={sourceUrl} target="_blank" rel="noopener noreferrer"
            aria-label={"Open " + label + " source"} title={"Open " + label + " evidence ↗"}>
            <Stage0PlatformMark platform={platform} />
          </a>
        : <span key={platform} className={cls} aria-label={label} title={label}>
            <Stage0PlatformMark platform={platform} />
          </span>;
    })}
  </div>;
}

function Stage0AgentBadge({ agents }: { agents: KeywordVolume["source_agents"] }) {
  if (!agents?.length) return <span className="rrugc-stage0-agent-empty" title="Chưa có dữ liệu agent lịch sử; không suy đoán từ Pinterest hoặc Google Ads">—</span>;
  const first = agents[0];
  const shortName = first.agent_name === "Agent-Reach Auto Discover" ? "VPS Reach"
    : first.agent_name === "Research Inbox" ? "Manual"
    : first.agent_name.toLowerCase().includes("keyword scout") ? "Scout"
    : first.agent_name.length > 13 ? first.agent_name.slice(0, 12) + "…" : first.agent_name;
  const details = agents.map(agent =>
    (agent.agent_name === "Agent-Reach Auto Discover" ? "CAM VPS · Agent-Reach Auto Discover" : agent.agent_name) + " · " + agent.platforms.join(", ") + " · " +
    agent.evidence_count + " source" + (agent.evidence_count === 1 ? "" : "s")
  ).join("\n");
  return <span className="rrugc-stage0-agent-badge" title={details} aria-label={"Found by " + agents.map(a => a.agent_name === "Agent-Reach Auto Discover" ? "CAM VPS · Agent-Reach Auto Discover" : a.agent_name).join(", ")}>
    <span className="rrugc-stage0-agent-symbol" aria-hidden="true">◎</span>
    <span className="rrugc-stage0-agent-label">{shortName}</span>
    {agents.length > 1 && <span className="rrugc-stage0-agent-more">+{agents.length - 1}</span>}
  </span>;
}

function formatTrendPeriod(value: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return value;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return date.toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
}

function Icon({
  name,
  filled = false,
}: {
  name: "search" | "star" | "badge-check" | "circle-plus" | "circle-check" | "thumbs-up" | "thumbs-down" | "undo" | "chart" | "money" | "competition" | "check" | "plus" | "image";
  filled?: boolean;
}) {
  if (name === "image") return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4 17 5-5 4 4 3-3 5 5"/></svg>;
  if (name === "search") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.2" /><path d="m15.2 15.2 4.3 4.3" /></svg>;
  if (name === "star") return <svg viewBox="0 0 24 24" aria-hidden="true" fill={filled ? "currentColor" : "none"}><path d="m12 2.8 2.8 5.7 6.3.9-4.6 4.5 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.5 6.3-.9z" /></svg>;
  if (name === "badge-check") return <svg data-icon="badge-check" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 2.6 1.4 3-.1 1.5 2.6 2.6 1.5-.1 3L23 12l-1.4 2.6.1 3-2.6 1.5-1.5 2.6-3-.1L12 23l-2.6-1.4-3 .1-1.5-2.6-2.6-1.5.1-3L1 12l1.4-2.6-.1-3 2.6-1.5 1.5-2.6 3 .1L12 2Z" transform="translate(1.7 1.7) scale(.86)" /><path d="m8.2 12 2.5 2.5 5.1-5.2" /></svg>;
  if (name === "circle-plus") return <svg data-icon="circle-plus" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></svg>;
  if (name === "circle-check") return <svg data-icon="circle-check" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="m8 12 2.6 2.6L16 9.5" /></svg>;
  if (name === "thumbs-up") return <svg data-icon="thumbs-up" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v10H4a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h3Zm0 0 4.8-7a1.8 1.8 0 0 1 3.2 1.4L14 9h5a3 3 0 0 1 3 3.6l-1.2 6a3 3 0 0 1-3 2.4H7" /></svg>;
  if (name === "thumbs-down") return <svg data-icon="thumbs-down" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 14V4H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h3Zm0 0 4.8 7a1.8 1.8 0 0 0 3.2-1.4L14 15h5a3 3 0 0 0 3-3.6l-1.2-6A3 3 0 0 0 17.8 3H7" /></svg>;
  if (name === "undo") return <svg data-icon="undo" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-2" /></svg>;
  if (name === "chart") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18V8m5 10v-5m5 5V5m5 13v-8" /></svg>;
  if (name === "money") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M14.8 8.8c-.5-.8-1.5-1.2-2.7-1.2-1.5 0-2.6.7-2.6 1.8 0 2.8 5.4 1.1 5.4 4 0 1.2-1.1 2-2.8 2-1.4 0-2.5-.5-3-1.4M12 6.2v11.6" /></svg>;
  if (name === "competition") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h3V9H4zm6 0h4V5h-4zm7 0h3v-6h-3z" /></svg>;
  if (name === "check") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6.5 12.4 3.3 3.3 7.7-8" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>;
}

const DETAIL_CHART_WIDTH = 720;
const DETAIL_CHART_HEIGHT = 224;
const DETAIL_PLOT_LEFT = 48;
const DETAIL_PLOT_RIGHT = 706;
const DETAIL_PLOT_TOP = 24;
const DETAIL_PLOT_BOTTOM = 190;

function detailTooltipPlacement(x: number, width = DETAIL_CHART_WIDTH): "is-left-edge" | "is-centered" | "is-right-edge" {
  const ratio = width > 0 ? x / width : .5;
  if (ratio <= .24) return "is-left-edge";
  if (ratio >= .76) return "is-right-edge";
  return "is-centered";
}

export function nearestTrendPointIndex(
  pointerClientX: number,
  chartLeft: number,
  chartWidth: number,
  pointCount: number,
): number {
  if (pointCount < 1 || chartWidth <= 0) return -1;
  if (pointCount === 1) return 0;
  const relativeX = (pointerClientX - chartLeft) / chartWidth * DETAIL_CHART_WIDTH;
  const ratio = (relativeX - DETAIL_PLOT_LEFT) / (DETAIL_PLOT_RIGHT - DETAIL_PLOT_LEFT);
  return Math.min(pointCount - 1, Math.max(0, Math.round(ratio * (pointCount - 1))));
}

function makeDetailChartGeometry(points: KeywordVolumeTrendPoint[]) {
  if (points.length < 2) return null;
  const values = points.map(point => Math.max(0, point.volume));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = Math.max(max - min, max * .08, 1);
  const lower = Math.max(0, min - spread * .3);
  const upper = Math.max(1, max + spread * .3);
  const graphHeight = DETAIL_PLOT_BOTTOM - DETAIL_PLOT_TOP;
  const dots = values.map((value, index) => ({
    x: DETAIL_PLOT_LEFT + index * (DETAIL_PLOT_RIGHT - DETAIL_PLOT_LEFT) / (values.length - 1),
    y: DETAIL_PLOT_BOTTOM - (value - lower) / (upper - lower) * graphHeight,
  }));
  // Horizontal-tangent cubic segments are smooth without overshooting a data point.
  const line = dots.reduce((path, dot, index) => {
    if (!index) return `M ${dot.x.toFixed(1)} ${dot.y.toFixed(1)}`;
    const previous = dots[index - 1];
    const control = (dot.x - previous.x) * .38;
    return path + ` C ${(previous.x + control).toFixed(1)} ${previous.y.toFixed(1)} ${(dot.x - control).toFixed(1)} ${dot.y.toFixed(1)} ${dot.x.toFixed(1)} ${dot.y.toFixed(1)}`;
  }, "");
  const area = line + ` L ${DETAIL_PLOT_RIGHT} ${DETAIL_PLOT_BOTTOM} L ${DETAIL_PLOT_LEFT} ${DETAIL_PLOT_BOTTOM} Z`;
  const ticks = [0, 1, 2, 3].map(index => ({
    y: DETAIL_PLOT_TOP + graphHeight * index / 3,
    label: Math.round(upper - (upper - lower) * index / 3).toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 }),
  }));
  return { dots, line, area, ticks };
}

function makeChartGeometry(points: KeywordVolumeTrendPoint[], width: number, height: number, pad = 4) {
  const source = points.length > 1 ? points : points.length === 1 ? [points[0], points[0]] : [];
  if (!source.length) return { line: "", area: "", dots: [] as Array<{ x: number; y: number }> };
  const values = source.map(point => Math.max(0, point.volume));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const usableW = width - pad * 2;
  const usableH = height - pad * 2;
  const dots = source.map((point, index) => ({
    x: pad + (source.length === 1 ? usableW / 2 : usableW * index / Math.max(1, source.length - 1)),
    y: pad + usableH - ((Math.max(0, point.volume) - min) / range) * usableH,
  }));
  const line = dots.map((dot, index) => (index ? "L" : "M") + dot.x.toFixed(1) + " " + dot.y.toFixed(1)).join(" ");
  const area = line
    ? line + " L " + dots[dots.length - 1].x.toFixed(1) + " " + (height - pad) + " L " + dots[0].x.toFixed(1) + " " + (height - pad) + " Z"
    : "";
  return { line, area, dots };
}

export function KeywordTrendChart({ item, onOpen }: { item: Pick<KeywordVolume, "keyword" | "search_volume" | "trend" | "three_month_change_pct" | "yoy_change_pct"> & Partial<Pick<KeywordVolume, "provider">>; onOpen: () => void }) {
  const trend = item.trend ?? [];
  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen();
    }
  };

  if (trend.length < 2) {
    return <div
      className="rrugc-stage0-trend rrugc-stage0-trend-empty"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
      aria-label={"Open details for " + item.keyword + ". Monthly trend unavailable."}
    >
      <span>{item.provider === "pending" ? "Awaiting AEBrowse" : "Trend unavailable"}</span>
      <small>Open keyword details</small>
    </div>;
  }

  const mini = makeChartGeometry(trend, 126, 38, 3);
  const large = makeChartGeometry(trend, 300, 118, 10);
  const volumes = trend.map(point => point.volume);
  const min = Math.min(...volumes);
  const max = Math.max(...volumes);
  const first = trend[0];
  const last = trend[trend.length - 1];

  return <div
    className="rrugc-stage0-trend"
    role="button"
    tabIndex={0}
    onClick={onOpen}
    onKeyDown={handleKeyDown}
    aria-label={"Open details and 12-month Google Ads trend for " + item.keyword}
  >
    <svg className="rrugc-stage0-sparkline" viewBox="0 0 126 38" preserveAspectRatio="none" aria-hidden="true">
      <path className="rrugc-stage0-spark-area" d={mini.area} />
      <path className="rrugc-stage0-spark-line" d={mini.line} />
      <circle cx={mini.dots[mini.dots.length - 1].x} cy={mini.dots[mini.dots.length - 1].y} r="2.2" />
    </svg>
    <span className="rrugc-stage0-trend-caption">{trend.length} mo</span>
    <div className="rrugc-stage0-trend-popover" role="tooltip">
      <div className="rrugc-stage0-trend-popover-head">
        <div><strong>{item.keyword}</strong><small>Google Ads monthly search volume</small></div>
        <b>{item.search_volume.toLocaleString()}</b>
      </div>
      <div className="rrugc-stage0-trend-changes">
        <span>3 mo <b className={changeTone(item.three_month_change_pct)}>{formatChange(item.three_month_change_pct)}</b></span>
        <span>YoY <b className={changeTone(item.yoy_change_pct)}>{formatChange(item.yoy_change_pct)}</b></span>
      </div>
      <svg viewBox="0 0 300 118" preserveAspectRatio="none" aria-hidden="true">
        <line x1="10" y1="30" x2="290" y2="30" />
        <line x1="10" y1="64" x2="290" y2="64" />
        <line x1="10" y1="98" x2="290" y2="98" />
        <path className="rrugc-stage0-spark-area" d={large.area} />
        <path className="rrugc-stage0-spark-line" d={large.line} />
        {large.dots.map((dot, index) => <circle key={index} cx={dot.x} cy={dot.y} r="2.5" />)}
      </svg>
      <div className="rrugc-stage0-trend-axis"><span>{first.period}</span><span>{last.period}</span></div>
      <div className="rrugc-stage0-trend-meta"><span>Low <b>{min.toLocaleString()}</b></span><span>High <b>{max.toLocaleString()}</b></span><span>Current <b>{item.search_volume.toLocaleString()}</b></span></div>
    </div>
  </div>;
}

export function KeywordDetailModal({
  item,
  onClose,
  onResolveMetrics,
  onSourceImageSaved,
}: {
  item: KeywordVolume | null;
  onClose: () => void;
  onResolveMetrics?: (keyword: string) => Promise<void>;
  onSourceImageSaved?: (keywordId: string, imageUrl: string) => void;
}) {
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [manualImageUrl, setManualImageUrl] = useState("");
  const [manualListingUrl, setManualListingUrl] = useState("");
  const [manualImageSaving, setManualImageSaving] = useState(false);
  const [manualImageNotice, setManualImageNotice] = useState("");
  const [manualImageError, setManualImageError] = useState("");
  const [metricsError, setMetricsError] = useState("");
  const [hoveredTrendIndex, setHoveredTrendIndex] = useState<number | null>(null);
  const chartGradientId = useId();

  // Live Stage 0/1 polling may replace the row object or onClose callback.
  // Reset only when the actual keyword changes, not on every refreshed render.
  useEffect(() => {
    setHoveredTrendIndex(null);
    setManualImageUrl("");
    setManualListingUrl("");
    setManualImageNotice("");
    setManualImageError("");
  }, [item?.id]);

  useEffect(() => {
    if (!item) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [item, onClose]);

  if (!item) return null;

  const marketplaceSources = agentSourceLinks(item).filter(source =>
    (source.platform === "amazon" || source.platform === "etsy") && source.url);
  const selectedSource = manualListingUrl || marketplaceSources[0]?.url || "";

  async function saveManualImage() {
    if (!item || manualImageSaving || !selectedSource || !manualImageUrl.trim()) return;
    setManualImageSaving(true);
    setManualImageError("");
    setManualImageNotice("");
    try {
      const saved = await attachKeywordSourceImage(
        item.id, selectedSource, manualImageUrl.trim(), Boolean(item.source_image_url),
      );
      onSourceImageSaved?.(item.id, saved.source_image_url);
      setManualImageNotice("Đã lưu ảnh nguồn công khai. Ảnh do người dùng cung cấp; nội dung chưa được tự xác thực từ listing.");
    } catch (error) {
      setManualImageError(error instanceof Error ? error.message : "Không lưu được URL ảnh nguồn.");
    } finally {
      setManualImageSaving(false);
    }
  }

  const trend = item.trend ?? [];
  const chart = makeDetailChartGeometry(trend);
  const volumes = trend.map(point => point.volume);
  const low = volumes.length ? Math.min(...volumes) : null;
  const high = volumes.length ? Math.max(...volumes) : null;
  const tailKind = keywordTailKind(item.keyword);
  const wordCount = item.keyword.trim().split(/\s+/).filter(Boolean).length;

  return <div
    className="rrugc-stage0-detail-backdrop"
    onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}
  >
    <section
      className="rrugc-stage0-detail-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rrugc-stage0-detail-title"
    >
      <header className="rrugc-stage0-detail-header">
        <div>
          <small>KEYWORD DETAIL · GOOGLE ADS</small>
          <h2 id="rrugc-stage0-detail-title">{item.keyword}</h2>
          <div className="rrugc-stage0-detail-badges">
            <span className={"tail-" + tailKind}>{keywordTailLabel(item.keyword)} · {wordCount} words</span>
            <span className={item.picked ? "is-used" : ""}>{item.picked ? "Used" : "Unused"}</span>
          </div>
        </div>
        <button type="button" className="rrugc-stage0-detail-close" onClick={onClose} aria-label="Close keyword details">×</button>
      </header>

      <div className="rrugc-stage0-detail-body">
        <div className="rrugc-stage0-detail-hero">
          <div className="rrugc-stage0-detail-source">
            {item.source_image_url
              ? <a href={item.source_pin_url || item.source_image_url} target="_blank" rel="noreferrer">
                  <DeferredImage src={item.source_image_url} alt={"Source for " + item.keyword}
                    loading="lazy" rootMargin="280px 0px" referrerPolicy="no-referrer" />
                  <span>Open source ↗</span>
                </a>
              : agentSourceLinks(item)[0]?.url
                ? <a className="rrugc-stage0-detail-source-link" href={agentSourceLinks(item)[0].url!} target="_blank" rel="noreferrer"><Icon name="search" /><span>Open source listing ↗</span></a>
                : <div className="rrugc-stage0-detail-source-empty"><Icon name="search" /><span>No source image</span></div>}
          </div>
          <div className="rrugc-stage0-detail-summary">
            <div>
              <small>Data source</small>
              <strong>{providerLabel(item.provider)}</strong>
              <span>Checked {formatDetailDate(item.fetched_at)}</span>
            </div>
            <div>
              <small>Marketplace / source references</small>
              <Stage0SourcePlatforms item={item} />
              <span>{item.request_count ? item.request_count.toLocaleString() + " provider requests" : "AEBrowse volume not queried yet"}</span>
            </div>
            <div>
              <small>Source agents</small>
              <strong>{item.source_agents?.length ? item.source_agents.map(agent => agent.agent_name === "Agent-Reach Auto Discover" ? "CAM VPS · Agent-Reach Auto Discover" : agent.agent_name).join(", ") : "Unknown (legacy)"}</strong>
              <span>{item.source_agents?.map(agent => `${agent.platforms.join(", ")} · ${agent.evidence_count} sources`).join(" | ") || "Agent ID not recorded for older keywords"}</span>
            </div>
          </div>
        </div>

        {marketplaceSources.length > 0 && <section className="rrugc-stage0-manual-source">
          <div className="rrugc-stage0-manual-source-heading">
            <Icon name="image"/>
            <div><strong>Ảnh nguồn Amazon / Etsy</strong>
              <small>Nhập URL ảnh CDN công khai của đúng listing. CAM kiểm tra link ảnh và giữ nguyên dữ liệu keyword.</small></div>
          </div>
          <div className="rrugc-stage0-manual-source-fields">
            <label>
              <span>Listing nguồn</span>
              <select value={selectedSource} onChange={event => setManualListingUrl(event.target.value)}
                disabled={manualImageSaving}>
                {marketplaceSources.map(source => <option key={source.url} value={source.url!}>
                  {platformDisplay(source.platform)} · {source.title || source.url}
                </option>)}
              </select>
            </label>
            <label>
              <span>URL ảnh sản phẩm (JPEG / PNG / WebP)</span>
              <input type="url" value={manualImageUrl}
                onChange={event => setManualImageUrl(event.target.value)}
                placeholder="https://i.etsystatic.com/... hoặc https://m.media-amazon.com/..."
                disabled={manualImageSaving} />
            </label>
            <button type="button" className="rrugc-stage0-manual-source-save" onClick={() => void saveManualImage()}
              disabled={manualImageSaving || !manualImageUrl.trim()}>
              <Icon name="image"/>{manualImageSaving ? "Đang kiểm tra..." : item.source_image_url ? "Thay ảnh nguồn" : "Lưu ảnh nguồn"}
            </button>
          </div>
          {manualImageError && <p className="rrugc-stage0-manual-source-error" role="alert">{manualImageError}</p>}
          {manualImageNotice && <p className="rrugc-stage0-manual-source-success" role="status">{manualImageNotice}</p>}
        </section>}

        {agentSourceLinks(item).length > 0 && <section className="rrugc-stage0-evidence" aria-label="Agent discovery evidence">
          <h4>Agent-Reach references <small>Search listing evidence · text not image-verified</small></h4>
          <div className="rrugc-stage0-evidence-list">{agentSourceLinks(item).map(source =>
            <a key={source.url} href={source.url!} target="_blank" rel="noreferrer" className="rrugc-stage0-evidence-item">
              <strong>{platformDisplay(source.platform)} ↗</strong>
              <span>{source.title || source.url}</span>
              {source.excerpt && <small>{source.excerpt}</small>}
            </a>
          )}</div>
        </section>}

        {item.provider === "pending" && <div className="rrugc-stage0-metrics-pending">
          <span>AEBrowse volume, competition, Trend and trademark screening have not been requested.</span>
          {onResolveMetrics && <button type="button" disabled={metricsLoading} onClick={() => {
            setMetricsLoading(true);
            setMetricsError("");
            void onResolveMetrics(item.keyword).catch(reason => {
              setMetricsError(reason instanceof Error ? reason.message : String(reason));
            }).finally(() => setMetricsLoading(false));
          }}>{metricsLoading ? "Checking AEBrowse…" : "Check metrics with AEBrowse"}</button>}
          {metricsError && <small role="alert">{metricsError}</small>}
        </div>}

        <section className="rrugc-stage0-detail-metrics" aria-label="Keyword metrics">
          <article><small>Avg searches / mo</small><strong>{item.provider === "pending" ? "—" : item.search_volume.toLocaleString()}</strong><span>{item.provider === "pending" ? "Awaiting AEBrowse lookup" : "Average monthly searches"}</span></article>
          <article><small>3-month change</small><strong className={changeTone(item.three_month_change_pct)}>{formatChange(item.three_month_change_pct)}</strong><span>vs 3 months ago</span></article>
          <article><small>YoY change</small><strong className={changeTone(item.yoy_change_pct)}>{formatChange(item.yoy_change_pct)}</strong><span>vs same month last year</span></article>
          <article><small>Competition</small><strong><span className={"rrugc-stage0-competition competition-" + competitionTone(item.competition)}>{item.competition || "—"}</span></strong><span>{typeof item.competition_index === "number" ? "Google Ads index " + item.competition_index : "Index unavailable"}</span></article>
          <article><small>Trademark (TM)</small><strong><TrademarkStatusBadge status={item.trademark_status} /></strong><span>{item.trademark_checked_at ? "Checked " + formatDetailDate(item.trademark_checked_at) : "USPTO verification pending"}</span></article>
          <article><small>Low CPC</small><strong>{formatCpc(item.cpc_low)}</strong><span>Top-of-page low bid</span></article>
          <article><small>High CPC</small><strong>{formatCpc(item.cpc_high)}</strong><span>Top-of-page high bid</span></article>
        </section>

        <section className="rrugc-stage0-tm-detail" aria-label="Trademark screening">
          <div>
            <strong>Trademark (TM) · AEBrowse screening</strong>
            <p><TrademarkStatusBadge status={item.trademark_status} /></p>
            <p>{item.trademark_advice || item.trademark_details || (item.trademark_status === "unverified" || !item.trademark_status
              ? "Trademark screening has not been returned by AEBrowse for this keyword."
              : "Provider screening result only. Review similar marks and applicable product categories.")}</p>
            {item.trademark_details && item.trademark_details !== item.trademark_advice ? <p>{item.trademark_details}</p> : null}
            {item.trademark_category && <p>Category: {item.trademark_category}</p>}
            {item.trademark_screened_keyword && <p>Screened query: {item.trademark_screened_keyword}</p>}
          </div>
          <div>
            <small>Source: {item.trademark_source || "Not checked"} · Conflicts: {item.trademark_match_count ?? "—"}</small>
            <small>Class 025: {item.trademark_class_025 == null ? "—" : item.trademark_class_025 ? "Matched" : "No match reported"}</small>
            <small>Checked: {formatDetailDate(item.trademark_checked_at)}</small>
            {item.trademark_primary_conflict && <small>Primary conflict: {JSON.stringify(item.trademark_primary_conflict)}</small>}
            {item.trademark_matches?.length ? <small>{item.trademark_matches.length} detailed matching records</small> : null}
            <a href="https://tmsearch.uspto.gov/" target="_blank" rel="noopener noreferrer">Verify with USPTO ↗</a>
          </div>
        </section>

        <section className="rrugc-stage0-detail-trend-panel">
          <div className="rrugc-stage0-detail-section-head">
            <div><small>TREND</small><h3>Monthly search volume</h3></div>
            {trend.length > 1 && <div className="rrugc-stage0-detail-trend-stats">
              <span>Low <b>{low?.toLocaleString()}</b></span>
              <span>High <b>{high?.toLocaleString()}</b></span>
              <span>Avg <b>{item.search_volume.toLocaleString()}</b></span>
            </div>}
          </div>
          {chart ? <>
            <div className="rrugc-stage0-detail-chart">
              <svg
                className="rrugc-stage0-detail-chart-svg"
                viewBox="0 0 720 224"
                preserveAspectRatio="none"
                tabIndex={0}
                role="img"
                aria-label={"Monthly search volume for " + item.keyword + ". Move anywhere across the chart to inspect months, or use the left and right arrow keys."}
                onPointerMove={event => {
                  const bounds = event.currentTarget.getBoundingClientRect();
                  const index = nearestTrendPointIndex(event.clientX, bounds.left, bounds.width, trend.length);
                  if (index >= 0) setHoveredTrendIndex(current => current === index ? current : index);
                }}
                onPointerLeave={() => setHoveredTrendIndex(null)}
                onFocus={() => setHoveredTrendIndex(current => current ?? trend.length - 1)}
                onBlur={() => setHoveredTrendIndex(null)}
                onKeyDown={event => {
                  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  setHoveredTrendIndex(current => {
                    if (event.key === "Home") return 0;
                    if (event.key === "End") return trend.length - 1;
                    const next = (current ?? trend.length - 1) + (event.key === "ArrowLeft" ? -1 : 1);
                    return Math.max(0, Math.min(trend.length - 1, next));
                  });
                }}
              >
                <defs>
                  <linearGradient id={chartGradientId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#4c79c2" stopOpacity=".22" />
                    <stop offset="100%" stopColor="#4c79c2" stopOpacity=".015" />
                  </linearGradient>
                </defs>
                <rect className="rrugc-stage0-detail-interaction" x="0" y="0" width="720" height="224" />
                {chart.ticks.map((tick, index) => <g key={index} className="rrugc-stage0-detail-grid">
                  <line x1={DETAIL_PLOT_LEFT} y1={tick.y} x2={DETAIL_PLOT_RIGHT} y2={tick.y} />
                  <text x="6" y={tick.y + 4}>{tick.label}</text>
                </g>)}
                <path className="rrugc-stage0-detail-area" d={chart.area} fill={"url(#" + chartGradientId + ")"} />
                <path className="rrugc-stage0-detail-line" d={chart.line} />
                {hoveredTrendIndex !== null && chart.dots[hoveredTrendIndex] && <>
                  <line className="rrugc-stage0-detail-guide" x1={chart.dots[hoveredTrendIndex].x}
                    y1={DETAIL_PLOT_TOP} x2={chart.dots[hoveredTrendIndex].x} y2={DETAIL_PLOT_BOTTOM} />
                  <circle className="rrugc-stage0-detail-point-halo"
                    cx={chart.dots[hoveredTrendIndex].x} cy={chart.dots[hoveredTrendIndex].y} r="11" />
                </>}
                {chart.dots.map((dot, index) => <circle key={trend[index].period}
                  className={"rrugc-stage0-detail-point" + (hoveredTrendIndex === index ? " is-active" : "")}
                  aria-label={formatTrendPeriod(trend[index].period) + ": " + trend[index].volume.toLocaleString() + " searches"}
                  cx={dot.x} cy={dot.y} r={hoveredTrendIndex === index ? 6 : 4} />)}
                {chart.dots.map((dot, index) => <text key={trend[index].period}
                  className="rrugc-stage0-detail-axis-month" x={dot.x} y="216" textAnchor="middle">
                    {formatTrendPeriod(trend[index].period)}
                  </text>)}
              </svg>
              {hoveredTrendIndex !== null && chart.dots[hoveredTrendIndex] && trend[hoveredTrendIndex] && <div
                role="tooltip"
                className={"rrugc-stage0-detail-chart-tooltip " + detailTooltipPlacement(chart.dots[hoveredTrendIndex].x) +
                  (chart.dots[hoveredTrendIndex].y < 80 ? " is-below" : "")}
                style={{
                  left: (chart.dots[hoveredTrendIndex].x / DETAIL_CHART_WIDTH * 100) + "%",
                  top: (chart.dots[hoveredTrendIndex].y / DETAIL_CHART_HEIGHT * 100) + "%",
                }}
              >
                <strong>{formatTrendPeriod(trend[hoveredTrendIndex].period)}</strong>
                <span>Monthly searches</span>
                <b>{trend[hoveredTrendIndex].volume.toLocaleString()}</b>
                <small>{hoveredTrendIndex === 0 ? "First month" : (() => {
                  const delta = trend[hoveredTrendIndex].volume - trend[hoveredTrendIndex - 1].volume;
                  return (delta > 0 ? "+" : "") + delta.toLocaleString() + " vs previous month";
                })()}</small>
              </div>}
            </div>
          </> : <div className="rrugc-stage0-detail-no-trend">
            <Icon name="chart" />
            <strong>No monthly history available</strong>
            <span>AEBrowse / Google Ads returned no monthly breakdown for this zero-volume or unsupported keyword.</span>
          </div>}
        </section>

        <details className="rrugc-stage0-detail-metadata">
          <summary>
            <span><small>RECORD</small><strong>Technical details</strong></span>
            <span className="rrugc-stage0-detail-metadata-summary">ID, provider account & timestamps</span>
          </summary>
          <dl>
            <div><dt>Keyword ID</dt><dd>{item.id}</dd></div>
            <div><dt>Provider</dt><dd>{providerLabel(item.provider)}</dd></div>
            <div><dt>Provider account</dt><dd>{item.provider_account || "—"}</dd></div>
            <div><dt>Customer ID</dt><dd>{item.provider_customer_id || "—"}</dd></div>
            <div><dt>Provider requests</dt><dd>{item.request_count?.toLocaleString() || "—"}</dd></div>
            <div><dt>Last checked</dt><dd>{formatDetailDate(item.fetched_at)}</dd></div>
            <div><dt>Last requested</dt><dd>{formatDetailDate(item.last_requested_at)}</dd></div>
            <div><dt>Added</dt><dd>{formatDetailDate(item.created_at)}</dd></div>
            <div><dt>Updated</dt><dd>{formatDetailDate(item.updated_at)}</dd></div>
            <div><dt>Used since</dt><dd>{formatDetailDate(item.picked_at)}</dd></div>
          </dl>
        </details>
      </div>
    </section>
  </div>;
}

function SortHeader({
  column,
  label,
  sortBy,
  sortDirection,
  onSortChange,
  className,
}: {
  column: KeywordAnalysisSortBy;
  label: string;
  className?: string;
  sortBy: KeywordAnalysisSortBy;
  sortDirection: KeywordAnalysisSortDirection;
  onSortChange: (column: KeywordAnalysisSortBy) => void;
}) {
  const active = sortBy === column;
  const nextDirection = active
    ? (sortDirection === "asc" ? "desc" : "asc")
    : (["keyword", "competition", "platform", "agent"].includes(column) ? "asc" : "desc");
  return <th className={className} aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
    <button
      type="button"
      className={"rrugc-stage0-sort" + (active ? " active rrugc-sort-" + sortDirection : "")}
      onClick={() => onSortChange(column)}
      aria-label={"Sort by " + label + " " + (nextDirection === "asc" ? "ascending" : "descending")}
      title={column === "trend" ? "Sort by verified AEBrowse 3-month growth; missing data last" : undefined}
    >
      <span>{label}</span>
      <SortDirectionIndicator active={active} direction={sortDirection} />
    </button>
  </th>;
}

function scoutFeedbackForScope(item: KeywordVolume, scope: ScoutFeedbackScope): ScoutFeedbackAction | "mixed" {
  const keyword = item.scout_keyword_feedback || "neutral";
  const pin = item.scout_pin_feedback || "neutral";
  if (scope === "keyword" || !item.source_pin_url) return keyword;
  if (scope === "pin") return pin;
  // A partially rated Keyword/Pin is still visibly rated; conflicting ratings
  // remain unselected until the operator chooses a target or overwrites both.
  if (keyword === pin || pin === "neutral") return keyword;
  if (keyword === "neutral") return pin;
  return "mixed";
}

export function KeywordAnalysisTable({
  data,
  query,
  loading = false,
  sortBy,
  sortDirection,
  usageFilter,
  tailFilter,
  suggestedOnly,
  pickingIds,
  feedbackUpdatingIds,
  onFeedbackChange,
  onResolveMetrics,
  onSortChange,
  onUsageFilterChange,
  onTailFilterChange,
  onSuggestedOnlyChange,
  onResetAll,
  onOpenResearchInbox,
  onOpenSearchIntelligence,
  onPickChange,
  onPageChange,
  onPageSizeChange,
  onQueryChange,
}: {
  data: KeywordVolumePage;
  query: string;
  loading?: boolean;
  sortBy: KeywordAnalysisSortBy;
  sortDirection: KeywordAnalysisSortDirection;
  usageFilter: KeywordUsageFilter;
  tailFilter: KeywordTailFilter;
  suggestedOnly: boolean;
  pickingIds: Set<string>;
  feedbackUpdatingIds: Set<string>;
  onFeedbackChange: (keywordId: string, action: ScoutFeedbackAction, scope: ScoutFeedbackScope) => void;
  onResolveMetrics?: (keyword: string) => Promise<void>;
  onSortChange: (column: KeywordAnalysisSortBy) => void;
  onUsageFilterChange: (filter: KeywordUsageFilter) => void;
  onTailFilterChange: (filter: KeywordTailFilter) => void;
  onSuggestedOnlyChange: (value: boolean) => void;
  onResetAll: () => void;
  onOpenResearchInbox?: () => void;
  onOpenSearchIntelligence?: () => void;
  onPickChange: (keywordId: string, picked: boolean) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onQueryChange: (query: string) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(data.total / Math.max(1, data.page_size)));
  const start = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const end = data.total === 0 ? 0 : Math.min(data.page * data.page_size, data.total);
  const activeOverviewFilter =
    suggestedOnly ? "suggested"
    : usageFilter === "used" ? "used"
        : tailFilter !== "all" ? tailFilter
          : "all";
  const [detailItem, setDetailItem] = useState<KeywordVolume | null>(null);
  const [sourceImageOverrides, setSourceImageOverrides] = useState<Record<string, string>>({});

  const selectOverviewFilter = (filter: "all" | "short" | "mid" | "long" | "used" | "suggested") => {
    const nextUsage: KeywordUsageFilter = filter === "used" ? "used" : "all";
    const nextTail: KeywordTailFilter =
      filter === "short" || filter === "mid" || filter === "long" ? filter : "all";
    const nextSuggestedOnly = filter === "suggested";

    if (usageFilter !== nextUsage) onUsageFilterChange(nextUsage);
    if (tailFilter !== nextTail) onTailFilterChange(nextTail);
    if (suggestedOnly !== nextSuggestedOnly) onSuggestedOnlyChange(nextSuggestedOnly);
  };

  const handleQueryChange = (nextQuery: string) => {
    if (nextQuery.trim()) selectOverviewFilter("all");
    onQueryChange(nextQuery);
  };

  return <>
  <section className="rrugc-card rrugc-stage0 rrugc-stage0-v2">
    <div className="rrugc-stage0-heading-row">
    <RrugcStageHeader
      className="rrugc-stage0-heading"
      kicker="STAGE 0 · KEYWORD INTELLIGENCE"
      title="Analysis Keyword"
      description="Quote Scout discovers hat phrases and Stage 0 combines Pinterest context with Google Ads demand, CPC, competition and observed search-volume trends."

    />

    <div className="rrugc-stage0-unified-toolbar">
    <div className="rrugc-stage0-controlbar">
      <div className="rrugc-stage0-searchbox"><KeywordSearchInput query={query} onQueryChange={handleQueryChange} /></div>

    </div>
      <div className="rrugc-stage0-header-tools">
        <button type="button" className="rrugc-stage0-intelligence-trigger rrugc-stage0-research-trigger"
          onClick={onOpenResearchInbox} disabled={!onOpenResearchInbox}
          aria-haspopup="dialog" aria-label="Open Research Intelligence" title="Agent-Reach · Research Intelligence">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 9h18M8 3v4M16 3v4M7 13h4m-4 3h7"/><circle cx="17.5" cy="15.5" r="2.5"/><path d="m19.5 17.5 2 2"/>
          </svg>
          <span>Research Intelligence</span>
        </button>
        <button type="button" className="rrugc-stage0-intelligence-trigger"
          onClick={onOpenSearchIntelligence} disabled={!onOpenSearchIntelligence}
          aria-haspopup="dialog" aria-label="Open Search Intelligence" title="Xem Search Intelligence và hiệu suất truy vấn">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5M7.5 10.5h6M10.5 7.5v6" /></svg>
          <span>Search Intelligence</span>
        </button>
      </div>
    </div>

    </div>

    <div className="rrugc-source-plan-kpis rrugc-stage0-kpis rrugc-stage0-kpis-restored" aria-label="Keyword overview filters">
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-total-kpi" + (activeOverviewFilter === "all" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "all"}
        onClick={() => selectOverviewFilter("all")}
      >
        <span>Total keywords</span>
        <strong>{data.overview.total_keywords.toLocaleString()}</strong>
        <small>{data.overview.total_search_volume.toLocaleString()} total monthly volume</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-short" + (activeOverviewFilter === "short" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "short"}
        onClick={() => selectOverviewFilter("short")}
      >
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="short" /></i><b>Short-tail</b></span>
        <strong>{data.overview.short_tail_keywords.toLocaleString()}</strong>
        <small>2-word keywords</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-mid" + (activeOverviewFilter === "mid" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "mid"}
        onClick={() => selectOverviewFilter("mid")}
      >
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="mid" /></i><b>Mid-tail</b></span>
        <strong>{data.overview.mid_tail_keywords.toLocaleString()}</strong>
        <small>3–4 word keywords</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-long" + (activeOverviewFilter === "long" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "long"}
        onClick={() => selectOverviewFilter("long")}
      >
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="long" /></i><b>Long-tail</b></span>
        <strong>{data.overview.long_tail_keywords.toLocaleString()}</strong>
        <small>5+ word keywords</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-used-kpi" + (activeOverviewFilter === "used" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "used"}
        onClick={() => selectOverviewFilter("used")}
      >
        <span>Used</span>
        <strong>{data.overview.picked_keywords.toLocaleString()}</strong>
        <small>Picked for use</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-suggested-kpi" + (activeOverviewFilter === "suggested" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "suggested"}
        onClick={() => selectOverviewFilter("suggested")}
      >
        <span>Đề xuất</span>
        <strong>{(data.overview.suggested_keywords ?? 0).toLocaleString()}</strong>
        <small>Keywords / Pins suggested</small>
      </button>
    </div>

    <div className="rrugc-stage0-table-wrap" role="region" aria-label="Stage 0 keyword analysis scrollable table" tabIndex={0}>
      <table className="rrugc-stage0-table rrugc-stage0-keyword-table" aria-busy={loading}
        data-sort-column={sortBy} data-sort-direction={sortDirection}>
        <thead><tr>
          <th>Preview</th>
          <SortHeader column="keyword" label="Keyword" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="trend" label="Trend" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="search_volume" label="Avg searches / mo" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="three_month_change" label="3-mo change" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="yoy_change" label="YoY change" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="competition" label="Competition" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="trademark" label="Trademark (TM)" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="cpc" label="Low CPC ($)" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="high_cpc" label="High CPC ($)" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader className="rrugc-stage0-platform-heading" column="platform" label="Platform" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader className="rrugc-stage0-agent-heading" column="agent" label="Agent" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="created_at" label="Created date" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <th>Action</th>
        </tr></thead>
        <tbody>
          {loading ? Array.from({ length: 6 }, (_, i) => <tr key={i} className="rrugc-stage0-skeleton-row"><td colSpan={14}><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-line" /></td></tr>) : data.items.map(originalItem => {
            const item = sourceImageOverrides[originalItem.id]
              ? { ...originalItem, source_image_url: sourceImageOverrides[originalItem.id] }
              : originalItem;
            const feedbackScope: ScoutFeedbackScope = item.source_pin_url ? "both" : "keyword";
            const feedbackStatus = scoutFeedbackForScope(item, feedbackScope);
            const hasFeedback = feedbackStatus !== "neutral";
            return <tr key={item.id} className={item.picked ? "is-picked" : ""}>
              <td className="rrugc-stage0-source-image">
                {item.source_image_url ? <a href={item.source_pin_url || agentSourceLinks(item)[0]?.url || item.source_image_url} target="_blank" rel="noreferrer" title={"Open source for " + item.keyword}><img src={item.source_image_url} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" /></a> : agentSourceLinks(item)[0]?.url ? <a className="rrugc-stage0-source-evidence-link" href={agentSourceLinks(item)[0].url!} target="_blank" rel="noreferrer" title={"Open source listing for " + item.keyword}><span aria-hidden="true">↗</span><small>View source</small></a> : <span className="rrugc-stage0-source-empty" title={item.source_image_status === "source_blocked" ? "Marketplace blocks automatic image access; open source listing" : item.source_image_status === "metadata_unavailable" ? "No usable public product image metadata" : "Product image not fetched yet"}>{item.source_image_status === "source_blocked" ? "Source blocked" : item.source_image_status === "metadata_unavailable" ? "No metadata" : "No image"}</span>}
              </td>
              <td className="rrugc-stage0-keyword-name">
                <button type="button" className="rrugc-stage0-keyword-link" onClick={() => setDetailItem(item)} title="Open keyword details">
                  <strong>{item.keyword}</strong>
                  <small>{providerLabel(item.provider)} · {item.provider === "pending" ? "added" : "checked"} {new Date(item.provider === "pending" ? item.created_at || item.fetched_at : item.fetched_at).toLocaleDateString()}</small>
                </button>
                <div className="rrugc-scout-feedback-control">
                  {hasFeedback && <span className={"rrugc-scout-feedback-state is-" + feedbackStatus} aria-label={"Trạng thái Scout: " + (feedbackStatus === "suggested" ? "Đã đề xuất" : feedbackStatus === "blocked" ? "Đã bỏ đề xuất" : "Đã phản hồi")}>
                    <Icon name={feedbackStatus === "suggested" ? "thumbs-up" : feedbackStatus === "blocked" ? "thumbs-down" : "check"} />
                    {feedbackStatus === "suggested" ? "Đã đề xuất" : feedbackStatus === "blocked" ? "Đã bỏ đề xuất" : "Đã phản hồi"}
                  </span>}
                  <div className="rrugc-scout-feedback-actions" role="group" aria-label={"Scout feedback: " + item.keyword}>
                    <button type="button" className={"is-suggested" + (feedbackStatus === "suggested" ? " active" : "")}
                      aria-pressed={feedbackStatus === "suggested"} aria-label={"Đề xuất cho Scout: " + item.keyword} title={feedbackStatus === "suggested" ? "Đã đề xuất cho Scout" : "Ưu tiên khám phá keyword hoặc Pin"}
                      disabled={feedbackUpdatingIds.has(item.id)}
                      onClick={() => onFeedbackChange(item.id, "suggested", feedbackScope)}>
                      <Icon name="thumbs-up" />Đề xuất
                    </button>
                    <button type="button" className={"is-blocked" + (feedbackStatus === "blocked" ? " active" : "")}
                      aria-pressed={feedbackStatus === "blocked"} aria-label={"Bỏ đề xuất cho Scout: " + item.keyword} title={feedbackStatus === "blocked" ? "Đã bỏ đề xuất · Ẩn sau 10 giây" : "Không tiếp tục khám phá keyword hoặc Pin"}
                      disabled={feedbackUpdatingIds.has(item.id)}
                      onClick={() => onFeedbackChange(item.id, "blocked", feedbackScope)}>
                      <Icon name="thumbs-down" />Bỏ
                    </button>
                    {hasFeedback && <button type="button" className="is-undo" aria-label={"Hoàn tác đề xuất: " + item.keyword}
                      title="Hoàn tác phản hồi" disabled={feedbackUpdatingIds.has(item.id)}
                      onClick={() => onFeedbackChange(item.id, "neutral", feedbackScope)}><Icon name="undo" /></button>}
                  </div>
                </div>
              </td>
              <td className="rrugc-stage0-trend-cell"><KeywordTrendChart item={item} onOpen={() => setDetailItem(item)} /></td>
              <td className="rrugc-stage0-volume">{item.provider === "pending" ? <><strong>—</strong><small>Chờ kiểm tra</small></> : <><strong>{item.search_volume.toLocaleString()}</strong><small>avg / month</small></>}</td>
              <td className="rrugc-stage0-change"><strong className={changeTone(item.three_month_change_pct)}>{formatChange(item.three_month_change_pct)}</strong></td>
              <td className="rrugc-stage0-change"><strong className={changeTone(item.yoy_change_pct)}>{formatChange(item.yoy_change_pct)}</strong></td>
              <td><span className={"rrugc-stage0-competition competition-" + competitionTone(item.competition)}>{item.competition || "—"}</span>{typeof item.competition_index === "number" && <small className="rrugc-stage0-competition-index">{item.competition_index}</small>}</td>
              <td className="rrugc-stage0-tm-cell"><TrademarkStatusBadge status={item.trademark_status} /></td>
              <td className="rrugc-stage0-cpc"><strong>{formatCpc(item.cpc_low)}</strong><small>low bid</small></td>
              <td className="rrugc-stage0-cpc"><strong>{formatCpc(item.cpc_high)}</strong><small>high bid</small></td>
              <td className="rrugc-stage0-platform-cell"><Stage0SourcePlatforms item={item} /></td>
              <td className="rrugc-stage0-agent-cell"><Stage0AgentBadge agents={item.source_agents} /></td>
              <td className="rrugc-stage0-created-date"><strong title={item.created_at ? new Date(item.created_at).toLocaleString() : "—"}>{formatTableDate(item.created_at)}</strong></td>
              <td className="rrugc-stage0-pick-cell">
                <div className="rrugc-stage0-row-actions-v2">
                  <button type="button" className={"rrugc-stage0-icon-action pick" + (item.picked ? " active" : "")} data-pick-state={item.picked ? "picked" : "unpicked"} aria-pressed={item.picked} aria-label={(item.picked ? "Mark unused: " : "Pick keyword: ") + item.keyword} title={item.picked ? "Đã Pick · Bấm để bỏ chọn" : "Chưa Pick · Bấm để chọn"} disabled={pickingIds.has(item.id)} onClick={() => onPickChange(item.id, !item.picked)}><Icon name={item.picked ? "circle-check" : "circle-plus"} /></button>
                </div>
              </td>
            </tr>;
          })}
          {!loading && data.items.length === 0 && <tr><td colSpan={14} className="rrugc-source-plan-empty">No keywords match these filters.</td></tr>}
        </tbody>
      </table>
    </div>

    <div className="rrugc-source-pagination rrugc-stage0-pagination">
      <span>{start}–{end} of {data.total}</span>
      <div className="rrugc-source-page-controls">
        <button type="button" disabled={loading || data.page <= 1} onClick={() => onPageChange(1)} aria-label="Stage 0 first page">«</button>
        <button type="button" disabled={loading || data.page <= 1} onClick={() => onPageChange(Math.max(1, data.page - 1))} aria-label="Stage 0 previous page">‹</button>
        <strong>Page {data.page} / {pageCount}</strong>
        <button type="button" disabled={loading || data.page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, data.page + 1))} aria-label="Stage 0 next page">›</button>
        <button type="button" disabled={loading || data.page >= pageCount} onClick={() => onPageChange(pageCount)} aria-label="Stage 0 last page">»</button>
      </div>
      <label>Rows<select aria-label="Stage 0 rows per page" value={data.page_size} disabled={loading} onChange={event => onPageSizeChange(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    </div>
  </section>
  <KeywordDetailModal
    item={(() => {
      const current = data.items.find(entry => entry.id === detailItem?.id) || detailItem;
      return current && sourceImageOverrides[current.id]
        ? { ...current, source_image_url: sourceImageOverrides[current.id] } : current;
    })()}
    onClose={() => setDetailItem(null)} onResolveMetrics={onResolveMetrics}
    onSourceImageSaved={(keywordId, imageUrl) => setSourceImageOverrides(previous => ({ ...previous, [keywordId]: imageUrl }))}
  />
  </>;
}