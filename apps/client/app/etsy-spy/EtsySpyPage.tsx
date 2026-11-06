import { useCallback, useEffect, useMemo, useState } from "react";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";
import { fetchAccessIdentity } from "../../features/access_management";
import {
  spyFetch, csvPath, readableTime,
  type SpyTab, type Page, type Summary, type Session, type Listing,
  type Keyword, type Trademark, type SpyLog, type Mark, type AgentStatus,
} from "./api";
import "./EtsySpyPage.css";

const TABS: { id: SpyTab; title: string }[] = [
  { id: "products", title: "Products" },
  { id: "keywords", title: "Keyword Tree" },
  { id: "trademarks", title: "Trademark" },
  { id: "logs", title: "System Logs" },
];
const SORTS: Record<SpyTab, readonly { id: string; text: string }[]> = {
  products: [
    { id: "keyword", text: "Keyword" }, { id: "sourceKind", text: "Source" },
    { id: "badge", text: "Etsy Badge + TM" }, { id: "bought24h", text: "Bought / 24h" },
    { id: "views24h", text: "Views / 24h" }, { id: "inCarts", text: "Carts" },
    { id: "pageTitle", text: "Product Title / URL" },
  ],
  keywords: [
    { id: "keyword", text: "Keyword" }, { id: "depth", text: "Depth" },
    { id: "priority", text: "Priority" }, { id: "status", text: "Status" },
    { id: "matchedCount", text: "Listings" },
  ],
  trademarks: [
    { id: "keyword", text: "Keyword / Title" }, { id: "status", text: "Status" },
    { id: "liveCount", text: "LIVE" }, { id: "checkedAt", text: "Checked" },
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
    : status === "unverified" ? "unverified" : "neutral";
}
function tmDescription(status: string) {
  return status === "no_match" ? "No match · not legal clearance"
    : status === "pending" ? "Pending"
    : status === "unverified" ? "Unverified" : status;
}
function EvidenceMarks({ matches }: { matches: Mark[] }) {
  if (!matches?.length) return <span className="etsy-muted">No trademark evidence attached</span>;
  return <div className="etsy-mark-items">
    {matches.slice(0, 30).map((item, index) => <div key={(item.serialNumber || item.markText) + index}>
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
  const hasMarks = Boolean(item.tmMatches?.length);
  return <tr>
    <td>{item.keyword}</td>
    <td><span className="etsy-pill">{item.sourceKind}</span></td>
    <td><div className="etsy-badge-stack">
      {item.badge || item.searchBadge ? <span className="etsy-pill etsy-demand">{item.badge || item.searchBadge}</span> : <span className="etsy-muted">—</span>}
      <button type="button" className={"etsy-pill tm-" + tmClass(item.tm)}
        title={[item.tmSource || "TM pending", item.tmCheckedAt ? readableTime(item.tmCheckedAt) : "",
          item.tmSourceAsOf ? "Source as of " + readableTime(item.tmSourceAsOf) : ""].filter(Boolean).join(" · ")}
        onClick={() => setExpanded(!expanded)} aria-expanded={expanded} disabled={!hasMarks}>
        {tmDescription(item.tm)}
      </button></div>
      {expanded && hasMarks && <EvidenceMarks matches={item.tmMatches} />}
    </td>
    <td className="etsy-num">{item.bought24h ?? "—"}</td>
    <td className="etsy-num">{item.views24h ?? "—"}</td>
    <td className="etsy-num">{item.inCarts ?? "—"}</td>
    <td className="etsy-title"><strong>{item.pageTitle}</strong>
      {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer">View on Etsy</a>}
      <small>{readableTime(item.observedAt)}</small>
    </td>
  </tr>;
}
function KeywordRow({ item }: { item: Keyword }) {
  const [open, setOpen] = useState(false);
  return <tr>
    <td><strong>{item.keyword}</strong>
      {item.parentId && <small>Parent: {item.parentId}</small>}
      {item.reason && <button className="etsy-details-trigger" onClick={() => setOpen(!open)}>Evidence {open ? "−" : "+"}</button>}
      {open && <pre className="etsy-evidence">{item.reason}
        {item.evidence != null ? "\n" + JSON.stringify(item.evidence, null, 2) : null}</pre>}
    </td>
    <td>{item.depth ?? "—"}</td><td className="etsy-num">{item.priority ?? "—"}</td>
    <td><span className="etsy-pill">{item.status || "pending"}</span></td>
    <td className="etsy-num">{item.matchedCount ?? "—"}</td>
  </tr>;
}
function TrademarkRow({ item }: { item: Trademark }) {
  const [expanded, setExpanded] = useState(false);
  return <tr>
    <td><strong>{item.keyword || item.title || "—"}</strong>
      {item.title && item.keyword && <small>{item.title}</small>}
      <small>{item.kind} · {item.source || "source unknown"}</small>
      {item.matched.length > 0 && <button className="etsy-details-trigger" onClick={() => setExpanded(!expanded)}>
        {expanded ? "Hide" : "Show"} {item.matched.length} marks
      </button>}
      {expanded && <EvidenceMarks matches={item.matched} />}
    </td>
    <td><span className={"etsy-pill tm-" + tmClass(item.status || "")}>{item.status || "pending"}</span>
      <small>{item.risk || "Not verified"}</small>
    </td>
    <td className="etsy-num">{item.liveCount} / {item.deadCount} / {item.unknownCount}</td>
    <td>{readableTime(item.checkedAt)}<small>Source: {readableTime(item.sourceAsOf)}</small></td>
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
  const [rows, setRows] = useState<Page<Listing | Keyword | Trademark | SpyLog> | null>(null);
  const [loading, setLoading] = useState(true);
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
    let alive = true;
    setLoading(true); setError("");
    const endpoint = { products: "/listings", keywords: "/keywords",
      trademarks: "/trademarks", logs: "/logs" }[options.tab];
    spyFetch<Page<Listing | Keyword | Trademark | SpyLog>>(endpoint + "?" + apiQuery(options).toString())
      .then(value => { if (alive) { setRows(value); setLoading(false); } })
      .catch(e => { if (alive) { setError(e instanceof Error ? e.message : "Request failed"); setLoading(false); } });
    return () => { alive = false; };
  }, [options, refresh]);
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
  return <main className="etsy-spy-page">
    <WorkspacePageHeader route="etsy-spy" actions={<>
      <span className={"etsy-connection " + (summary?.source_status || "never_synced")}>{syncLabel}</span>
      <button className="etsy-btn" onClick={() => setRefresh(n => n + 1)}>Refresh</button>
      <WorkspaceBackToAssets />
    </>} />
    <div className="etsy-freshness">
      Last synchronized (GMT+7): <strong>{readableTime(summary?.last_synced_at)}</strong> ·
      {summary?.online_agents || 0}/{summary?.agent_count || 0} agents online ·
      <span>Windows is the source of truth. Offline does not delete synchronized data.</span>
    </div>
    <section className="etsy-stats" aria-label="Etsy Spy overview">
      {[
        ["Eligible listings", summary?.listings], ["Keyword nodes", summary?.keywords],
        ["Sessions", summary?.sessions], ["Search / Shop", summary ? summary.search + " / " + summary.shop : null],
        ["TM pending", summary?.tm_pending], ["Errors / Warnings", summary ? summary.errors + " / " + summary.warnings : null],
      ].map(([label, value]) => <div className="etsy-stat" key={String(label)}>
        <span>{label}</span><strong>{value ?? "—"}</strong>
      </div>)}
    </section>
    <div className="etsy-toolbar">
      <nav role="tablist" aria-label="Etsy Spy sections" className="etsy-tabs">
        {TABS.map(item => <button key={item.id} role="tab" aria-selected={activeTab === item.id}
          className={activeTab === item.id ? "active" : ""} onClick={() => {
            setTypedSearch("");
            update({
              tab: item.id, q: "", sessionId: "", source: "", badge: "",
              tm: "", status: "", level: "", phase: "",
              sort: item.id === "products" ? "observedAt"
                : item.id === "logs" ? "at" : item.id === "trademarks" ? "checkedAt" : "updatedAt",
              dir: "desc", page: 1,
            });
          }}>{item.title}</button>)}
      </nav>
      <div className="etsy-actions">
        <label>Session
          <select value={options.sessionId} onChange={event => update({ sessionId: event.target.value })}>
            <option value="">All sessions</option>
            {sessions.map(item => <option key={item.id} value={item.id}>
              {item.keyword || item.id} · {item.status || "unknown"}
            </option>)}
          </select>
        </label>
        {activeTab === "products" || activeTab === "logs" ? <a className="etsy-btn"
          href={csvPath(activeTab, exportQuery)} download>Export CSV</a> : null}
      </div>
    </div>
    <section className="etsy-search-filters" aria-label="Search and filters">
      <input type="search" aria-label="Search Spy records" placeholder={
        activeTab === "logs" ? "Search message or stack" : "Search keyword or product title"
      } value={typedSearch} onChange={event => setTypedSearch(event.target.value)} />
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
      <label>Rows
        <select value={options.pageSize} onChange={event => update({ pageSize: Number(event.target.value) })}>
          {[50, 100, 200].map(n => <option value={n} key={n}>{n}</option>)}
        </select>
      </label>
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
            setTmResult(result.matched);
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
    <div className="etsy-table-card">
      <div className="etsy-table-top"><strong>{TABS.find(x => x.id === activeTab)?.title}</strong>
        <span>{rows ? rows.total.toLocaleString() : "—"} records</span></div>
      {loading ? <div role="status" className="etsy-skeleton">Loading synchronized records…</div>
        : !rows?.items.length ? <div className="etsy-empty">No matching records. {summary?.source_status === "never_synced"
          ? "Pair the Windows agent to begin the initial historical sync." : "Try adjusting filters."}</div>
        : <div className="etsy-table-wrap">
          <table><thead><tr>
            {columns.map(col => <th key={col.id}>
              <button type="button" onClick={() => sortColumn(col.id)}
                aria-label={"Sort by " + col.text}
                aria-sort={options.sort === col.id ? (options.dir === "asc" ? "ascending" : "descending") : undefined}>
                {col.text}{options.sort === col.id ? (options.dir === "asc" ? " ↑" : " ↓") : ""}
              </button>
            </th>)}
            {activeTab === "logs" && <th>Message / Stack</th>}
          </tr></thead><tbody>
            {activeTab === "products" ? (rows.items as Listing[]).map(item =>
              <ProductRow key={item.sessionId + "/" + item.keyword + "/" + item.sourceKind + "/" + item.listingKey} item={item} />)
              : activeTab === "keywords" ? (rows.items as Keyword[]).map(item =>
                <KeywordRow key={item.sessionId + "/" + item.id} item={item} />)
              : activeTab === "trademarks" ? (rows.items as Trademark[]).map(item =>
                <TrademarkRow key={item.checkId} item={item} />)
              : (rows.items as SpyLog[]).map(item => <LogRow key={item.eventId} item={item} />)}
          </tbody></table>
        </div>}
      <div className="etsy-pager">
        <span>Page {options.page} / {totalPages}</span>
        <button disabled={loading || options.page <= 1} onClick={() => update({ page: options.page - 1 })}>Previous</button>
        <button disabled={loading || options.page >= totalPages} onClick={() => update({ page: options.page + 1 })}>Next</button>
      </div>
    </div>
    <section className="etsy-agents" aria-label="Windows synchronization agents">
      <div><h2>Windows Sync Agent</h2><p>Data remains available even when the source PC is offline.</p></div>
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
    </section>
  </main>;
}
