import { useCallback, useEffect, useMemo, useState } from "react";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";
import { fetchAccessIdentity } from "../../features/access_management";
import {
  spyFetch, csvPath, readableTime,
  type SpyTab, type Page, type Summary, type Session, type Listing,
  type Keyword, type Trademark, type SpyLog, type Mark, type AgentStatus,
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
    { id: "pageTitle", text: "Product / Etsy" }, { id: "keyword", text: "Keyword" },
    { id: "sourceKind", text: "Source" }, { id: "badge", text: "Etsy Badge + TM" },
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
  sessionId: string; source: string; badge: string; tm: string;
  status: string; level: string; phase: string; sort: string; dir: "asc" | "desc";
};
const baseOptions: Options = {
  tab: "products", page: 1, pageSize: 50, q: "",
  sessionId: "", source: "", badge: "", tm: "", status: "", level: "", phase: "",
  sort: "observedAt", dir: "desc",
};
function fromLocation(): Options {
  if (typeof window === "undefined") return baseOptions;
  const p = new URLSearchParams(window.location.search);
  const tab = TABS.find(t => t.id === p.get("tab"))?.id || "products";
  return {
    ...baseOptions, tab,
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
  result.delete("source");
  result.delete("badge");
  result.delete("tm");
  result.delete("status");
  result.delete("level");
  result.delete("phase");
  if (o.tab === "products") {
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
  "M15.5 7.5a5 5 0 1 0-1 4.2L21 18v3h-3v-3h-3v-3l-1.7-1.7",
  "m12 2 10 5-10 5L2 7l10-5Zm-10 10 10 5 10-5M2 17l10 5 10-5",
  "M2 3h2l2.5 11h12L21 6H5M9 20h.01M18 20h.01",
  "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 5v5l3 2",
  "M10.3 3.5 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.5a2 2 0 0 0-3.4 0ZM12 9v4m0 4h.01",
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
function ProductRow({ item }: { item: Listing }) {
  const [expanded, setExpanded] = useState(false);
  const tmMatches = safeMarks(item.tmMatches);
  const hasMarks = tmMatches.length > 0;
  const deadMatches = tmMatches.filter(mark => mark.liveStatus?.toUpperCase() === "DEAD").length;
  const tmTooltip = [item.tmSource || "TM status", item.tmCheckedAt ? readableTime(item.tmCheckedAt) : "",
    item.tmSourceAsOf ? "Source as of " + readableTime(item.tmSourceAsOf) : ""].filter(Boolean).join(" · ");
  const tmLabel = tmDescription(item.tm, deadMatches);
  const sourceKinds = item.sourceKinds?.length ? item.sourceKinds : item.metadata?.sourceKinds || [item.sourceKind];
  const sourceLabel = sourceKinds.includes("search") && sourceKinds.includes("shop")
    ? "Search + Shop" : sourceKinds.includes("shop") ? "Shop" : "Search";
  return <tr className="etsy-product-row">
    <td className="etsy-title">
      <div className="etsy-product-identity">
        <span className="etsy-product-glyph" aria-hidden="true">E</span>
        <div className="etsy-product-copy">
          <strong title={item.pageTitle}>{item.pageTitle}</strong>
          <div className="etsy-title-meta">
            <small>#{item.listingKey}</small>
            {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer">View on Etsy <span aria-hidden="true">↗</span></a>}
          </div>
        </div>
      </div>
    </td>
    <td className="etsy-keyword-cell"><strong title={item.keyword}>{item.keyword}</strong></td>
    <td><span className={"etsy-pill etsy-source-" + (sourceKinds.includes("search") ? "search" : "shop")}>{sourceLabel}</span></td>
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
    <td className="etsy-num">{item.inCarts ?? "—"}</td>
    <td className="etsy-time">{readableTime(item.observedAt)}</td>
    <td className="etsy-action-cell">{item.url
      ? <a href={item.url} target="_blank" rel="noopener noreferrer" aria-label={"Open Etsy listing: " + item.pageTitle} title="Open on Etsy">↗</a>
      : <span className="etsy-muted">—</span>}</td>
  </tr>;
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
  const [summary, setSummary] = useState<Summary | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
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
  const requestKey = options.tab + "?" + apiQuery(options).toString() + "#" + refresh;
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
      spyFetch<{ items: Session[] }>("/sessions"),
      spyFetch<{ items: AgentStatus[] }>("/agents"),
      fetchAccessIdentity(),
    ]).then(([overview, selected, currentAgents, identity]) => {
      if (!alive) return;
      setSummary(overview); setSessions(selected.items); setAgents(currentAgents.items);
      setCanManage(identity.permissions.includes("etsy_spy.manage"));
    }).catch(e => { if (alive) setError(e instanceof Error ? e.message : "Cannot load overview"); });
    return () => { alive = false; };
  }, [refresh]);
  useEffect(() => {
    // Keyword Tree loads all source pages in its own panel, not a single paged slice.
    if (options.tab === "keywords") return;
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
  const exportQuery = apiQuery(options);
  exportQuery.delete("page"); exportQuery.delete("page_size");
  const activeTab = options.tab;
  const selectTab = (tab: SpyTab, filters: Partial<Options> = {}) => {
    setTypedSearch("");
    update({
      tab, q: "", sessionId: "", source: "", badge: "", tm: "",
      status: "", level: "", phase: "",
      sort: tab === "products" ? "observedAt" : tab === "logs" ? "at"
        : tab === "trademarks" ? "checkedAt" : "updatedAt",
      dir: "desc", page: 1, ...filters,
    });
  };
  const overviewCards: {
    title: string; value: number | string | null | undefined; hint: string;
    tone: string; tab: SpyTab; filters?: Partial<Options>; selected: boolean;
  }[] = [
    { title: "Eligible listings", value: summary?.listings, hint: "Current Etsy products",
      tone: "total", tab: "products",
      selected: activeTab === "products" && !options.source && !options.tm },
    { title: "Keyword nodes", value: summary?.keywords, hint: "Discovery branches",
      tone: "keywords", tab: "keywords", selected: activeTab === "keywords" },
    { title: "Search", value: summary?.search, hint: "Found in Etsy Search",
      tone: "search", tab: "products", filters: { source: "search" },
      selected: activeTab === "products" && options.source === "search" },
    { title: "Shop", value: summary?.shop, hint: "Found in Etsy Shop",
      tone: "shop", tab: "products", filters: { source: "shop" },
      selected: activeTab === "products" && options.source === "shop" },
    { title: "TM pending", value: summary?.tm_pending, hint: "Awaiting evidence",
      tone: "pending", tab: "products", filters: { tm: "pending" },
      selected: activeTab === "products" && options.tm === "pending" },
    { title: "TM checked", value: summary?.tm_checked, hint: "Checked products",
      tone: "checked", tab: "trademarks", selected: activeTab === "trademarks" },
    { title: "Errors", value: summary?.errors, hint: `${summary?.warnings ?? "—"} warnings in logs`,
      tone: "errors", tab: "logs", filters: { level: "error" },
      selected: activeTab === "logs" && options.level === "error" },
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
            <div className="etsy-freshness" role="status">
              <span>Last synced (GMT+7) <strong>{readableTime(summary?.last_synced_at)}</strong></span>
              <span className="etsy-freshness-divider" aria-hidden="true" />
              <span><strong>{summary?.online_agents ?? 0}/{summary?.agent_count ?? 0}</strong> agents online</span>
              {summary?.last_inventory_at && <>
                <span className="etsy-freshness-divider" aria-hidden="true" />
                <span>Listings reconciled: <strong>{readableTime(summary.last_inventory_at)}</strong></span>
              </>}
            </div>
          </div>
          <div className="etsy-stage-heading-actions">
            <div className="etsy-search-box">
              <svg className="etsy-search-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
              <input type="search" aria-label="Search Spy records"
                placeholder={activeTab === "logs" ? "Search message or stack…" : "Search keyword or product…"}
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
          </button>)}
        </section>
        <section className="etsy-search-filters" aria-label="Search and filters">
          <span className="etsy-filter-label">FILTERS</span>
      {activeTab === "products" ? <>
        <select aria-label="Source" value={options.source} onChange={event => update({ source: event.target.value })}>
          <option value="">All sources</option><option value="search">Search</option><option value="shop">Shop</option>
        </select>
        <select aria-label="Etsy badge" value={options.badge} onChange={event => update({ badge: event.target.value })}>
          <option value="">All Etsy badges</option><option value="Bestseller">Bestseller</option>
          <option value="Popular now">Popular now</option>
        </select>
        <select aria-label="Trademark status" value={options.tm} onChange={event => update({ tm: event.target.value })}>
          <option value="">All TM states</option><option value="LIVE">LIVE</option>
          <option value="DEAD">DEAD</option><option value="no_match">No match</option>
          <option value="pending">Pending</option><option value="unverified">Unverified</option>
        </select>
      </> : activeTab === "keywords" || activeTab === "trademarks" ? <select
        aria-label="Status filter" value={options.status} onChange={event => update({ status: event.target.value })}>
        <option value="">All statuses</option>
        {(activeTab === "keywords" ? ["pending", "running", "done", "completed", "failed", "skipped"]
          : ["pending", "running", "complete", "cached", "unavailable", "checked"]).map(value =>
          <option value={value} key={value}>{value}</option>)}
      </select> : <>
        <select aria-label="Log level" value={options.level} onChange={event => update({ level: event.target.value })}>
          <option value="">All levels</option>{["error", "warning", "info", "debug"].map(value =>
            <option value={value} key={value}>{value}</option>)}
        </select>
        <input aria-label="Log source" placeholder="Log source" value={options.source}
          onChange={event => update({ source: event.target.value })} />
        <input aria-label="Log phase" placeholder="Phase" value={options.phase}
          onChange={event => update({ phase: event.target.value })} />
      </>}
      {(typedSearch || options.source || options.badge || options.tm || options.status || options.level || options.phase) && <button
        type="button" className="etsy-clear-filters" onClick={() => {
          setTypedSearch("");
          update({ q: "", source: "", badge: "", tm: "", status: "", level: "", phase: "" });
        }}>Clear filters</button>}
      <div className="etsy-actions">
        <label>Session
          <select value={options.sessionId} onChange={event => update({ sessionId: event.target.value })}>
            <option value="">All sessions</option>
            {sessions.map(item => <option key={item.id} value={item.id}>
              {item.keyword || item.id} · {item.status || "unknown"}
            </option>)}
          </select>
        </label>
        {activeTab === "products" || activeTab === "logs" ? <a className="etsy-btn etsy-export"
          href={csvPath(activeTab, exportQuery)} download><span aria-hidden="true">↓</span> Export CSV</a> : null}
      </div>
    </section>
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
    {error && <div role="alert" className="etsy-error">Cannot load Etsy Spy data: {error}
      <button onClick={() => setRefresh(n => n + 1)}>Retry</button></div>}
    <div className="etsy-table-section-header">
      <h2>{TABS.find(x => x.id === activeTab)?.title} <span>({activeTab === "keywords"
        ? (options.sessionId ? "Session" : summary?.keywords?.toLocaleString() ?? "—")
        : rows ? rows.total.toLocaleString() : "—"})</span></h2>
      <span className="etsy-table-count">{activeTab === "keywords"
        ? "Hierarchical view · expand or collapse any keyword branch"
        : "Sorted and filtered from synchronized Windows data"}</span>
    </div>
    {activeTab === "keywords" ?
      <KeywordTreeView sessionId={options.sessionId} search={options.q} status={options.status}
        sort={options.sort} direction={options.dir} refresh={refresh} onSort={sortColumn} />
      : <div className="etsy-table-card">
      {loading ? <div role="status" className="etsy-skeleton">Loading synchronized records…</div>
        : !rows?.items.length ? <div className="etsy-empty">No matching records. {summary?.source_status === "never_synced"
          ? "Pair the Windows agent to begin the initial historical sync." : "Try adjusting filters."}</div>
        : <div className="etsy-table-wrap">
          <table className={"etsy-data-table is-" + activeTab}><thead><tr>
            {columns.map(col => <th key={col.id}
              aria-sort={options.sort === col.id ? (options.dir === "asc" ? "ascending" : "descending") : undefined}>
              <button type="button" onClick={() => sortColumn(col.id)}
                aria-label={"Sort by " + col.text}>
                {col.text}{options.sort === col.id ? (options.dir === "asc" ? " ↑" : " ↓") : ""}
              </button>
            </th>)}
            {activeTab === "logs" && <th>Message / Stack</th>}
            {activeTab === "products" && <th className="etsy-action-header">Action</th>}
          </tr></thead><tbody>
            {activeTab === "products" ? (rows.items as Listing[]).map(item =>
              <ProductRow key={item.sessionId + "/" + item.keyword + "/" + item.sourceKind + "/" + item.listingKey} item={item} />)
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
      </div>
    </section>
  </main>;
}
