import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Activity, Check, CircleAlert, CirclePause, CirclePlay, Clock3, Copy, FileText, LockKeyhole, Pause, Play, RefreshCcw, RotateCcw, Search, Settings2, ShieldCheck, Terminal, X } from "lucide-react";

type ModeName = "review" | "keyword";
type ModeInfo = { mode: ModeName; state: string; pid: number | null; desired: boolean; restarts: number; lastError: string | null };
type KeywordSummary = { total_keywords: number; added_24h: number; added_7d: number; analysis_pending: number; analysis_oldest_wait_seconds: number; analysis_backpressure_active: boolean; keyword_fair_share_limited: boolean; keyword_next_slot_seconds: number; gemini_backup_keys_configured: number; last_created_at: string | null; last_updated_at: string | null; fetched_at: string };
type OperationsSummary = {
  review: {
    scout_active: number; scout_completed_24h: number; scout_failed_24h: number;
    stage1_pending: number; stage1_running: number; stage1_completed_24h: number; stage1_failed_24h: number;
  };
  keyword: {
    active_searches: number; tenant_active_searches: number; ready_queries: number;
    total_queries: number; completed_cycles_total: number; failed_cycles_total: number;
    suggestions_pending: number; new_keywords_24h: number;
    scanned_pins_24h_agent: number; saved_keywords_24h_agent: number;
  };
  gemini: {
    primary_configured: boolean; backup_keys: number; configured_keys: number;
    capacity_available: boolean; failover_enabled: boolean; strategy: string;
  };
  fetched_at: string;
};
type Dashboard = { managerVersion: string; version: string; commit: string; updateState: string; paired: boolean; updating: boolean; controllerAvailable: boolean; automationEnabled: boolean; modes: ModeInfo[] };
const EMPTY: Dashboard = { managerVersion: "—", version: "Checking…", commit: "—", updateState: "Connecting to runtime", paired: false, updating: false, controllerAvailable: true, automationEnabled: false, modes: [
  { mode: "review", state: "Stopped", pid: null, desired: false, restarts: 0, lastError: null },
  { mode: "keyword", state: "Stopped", pid: null, desired: false, restarts: 0, lastError: null },
] };
const isNative = typeof window !== "undefined" && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);

function JobCounters({ rows, title }: { title: string; rows: Array<[string, number | undefined, "ok" | "warn" | "normal"]> }) {
  return <div className="scout-jobs" aria-label={title}>
    <div className="scout-jobs-heading"><strong>{title}</strong><small>Server · live counts</small></div>
    <div className="scout-jobs-grid">{rows.map(([label, value, level]) =>
      <div className={"scout-job " + level} key={label}><small>{label}</small><b>{value == null ? "—" : value.toLocaleString("en-US")}</b></div>
    )}</div>
  </div>;
}

function Card({ info, version, commit, busy, automationEnabled, keywordSummary, keywordError, operations, operationsError, onAction }: {
  info: ModeInfo; version: string; commit: string; busy: boolean; automationEnabled: boolean;
  keywordSummary?: KeywordSummary | null; keywordError?: string | null;
  operations?: OperationsSummary | null; operationsError?: string | null;
  onAction: (cmd: "start" | "stop" | "restart", mode: ModeName) => void;
}) {
  const active = info.state === "Running";
  const failed = /fatal|fail|attention|error|pairing/i.test(info.state);
  const Icon = info.mode === "review" ? FileText : Search;
  return <article className="scout-card">
    <div className="scout-top">
      <div className="scout-identity"><div className={"mode-icon " + info.mode}><Icon size={24}/></div><div><h2>{info.mode === "review" ? "Review Scout" : "Keyword Scout"}</h2><p>{info.mode === "review" ? "Monitors image references and review contexts" : "Discovers quotes and keyword opportunities"}</p></div></div>
      <span className={"status-pill " + (active ? "success" : failed ? "danger" : "neutral")}><span className="status-dot"/>{info.state}</span>
    </div>
    <div className="meta-row"><div><span>PID</span><b>{info.pid ?? "—"}</b></div><div><span>Scout version</span><b>{version}</b></div><div><span>Commit</span><b>{commit}</b></div></div>
    {info.mode === "keyword" && <div className="keyword-health">
      <div className="keyword-metric"><span>Total saved</span><strong>{keywordSummary ? keywordSummary.total_keywords.toLocaleString("en-US") : "—"}</strong></div>
      <div className="keyword-metric"><span>Added 24h</span><strong>+{keywordSummary?.added_24h ?? "—"}</strong></div>
      <div className="keyword-metric"><span>Added 7d</span><strong>+{keywordSummary?.added_7d ?? "—"}</strong></div>
      <div className="keyword-metric last"><span>Last saved</span><strong>{keywordSummary?.last_created_at ? new Date(keywordSummary.last_created_at).toLocaleString() : "No data"}</strong></div>
      {keywordSummary?.analysis_backpressure_active && <div className="keyword-health-note">Keyword Gemini fair-share: {keywordSummary.keyword_fair_share_limited ? "waiting " + keywordSummary.keyword_next_slot_seconds + "s" : "slot ready"} · backup keys configured: {keywordSummary.gemini_backup_keys_configured}</div>}
      <div className={"keyword-health-note " + (keywordSummary?.analysis_backpressure_active ? "backpressure" : "")}>AI analysis queue: {keywordSummary ? keywordSummary.analysis_pending.toLocaleString("en-US") + " pending · oldest " + Math.floor(keywordSummary.analysis_oldest_wait_seconds / 60) + " min" : "checking server…"}{keywordSummary?.analysis_backpressure_active ? " · Gemini capacity protection active" : ""}</div>
      {keywordError && <div className="keyword-health-note">{keywordError}</div>}
      {!keywordError && keywordSummary && info.state === "Running" && keywordSummary.last_created_at && Date.now() - new Date(keywordSummary.last_created_at).getTime() > 60 * 60 * 1000 && <div className="keyword-health-note">Running, but no new keywords in the last {Math.floor((Date.now() - new Date(keywordSummary.last_created_at).getTime()) / 3600000)}h · check AI/Pin logs</div>}
      {!keywordError && keywordSummary && keywordSummary.added_24h === 0 && info.state !== "Running" && <div className="keyword-health-note">No new keyword in the last 24 hours</div>}
    </div>}
    {info.mode === "review"
      ? <>
          <JobCounters title="Stage 1 AI jobs" rows={[
            ["Queued / retry", operations?.review.stage1_pending, "normal"],
            ["Processing", operations?.review.stage1_running, "normal"],
            ["Done · 24h", operations?.review.stage1_completed_24h, "ok"],
            ["Failed · 24h", operations?.review.stage1_failed_24h, "warn"],
          ]}/>
          <div className="scout-jobs-foot">Pinterest scan jobs: {operations ? operations.review.scout_active + " active · " + operations.review.scout_completed_24h + " done / 24h · " + operations.review.scout_failed_24h + " failed / 24h" : "Waiting for server…"}</div>
        </>
      : <>
          <JobCounters title="Keyword search jobs" rows={[
            ["Active · this agent", operations?.keyword.active_searches, "normal"],
            ["Ready queries", operations?.keyword.ready_queries, "normal"],
            ["Done · total", operations?.keyword.completed_cycles_total, "ok"],
            ["Failed · total", operations?.keyword.failed_cycles_total, "warn"],
          ]}/>
          <div className="scout-jobs-foot">Priority suggestions: {operations?.keyword.suggestions_pending ?? "—"} · Query pool: {operations?.keyword.total_queries ?? "—"} · New keywords / 24h: {operations?.keyword.new_keywords_24h ?? "—"}</div>
        </>
    }
    <div className="scout-gemini-status" role="status">
      <span className={"gemini-status-indicator " + (operations?.gemini.capacity_available ? "ready" : operations ? "limited" : "")}/>
      {operations
        ? <>Gemini: <strong>{operations.gemini.configured_keys ? operations.gemini.capacity_available ? "Quota available" : "Quota limited / unavailable" : "No key"}</strong>
          <span> · {operations.gemini.configured_keys} key(s), {operations.gemini.backup_keys} backup · {operations.gemini.failover_enabled ? "Auto failover" : "No backup configured"}</span></>
        : "Checking Gemini key pool…"}
    </div>
    {operationsError && <div className="scout-jobs-error">{operationsError}</div>}
    <div className="chips"><div><RefreshCcw/><span>Auto restart</span></div><div><ShieldCheck/><span>Browser watchdog</span></div><div><LockKeyhole/><span>Isolated profile</span></div></div>
    {info.lastError && <div className="inline-error" title={info.lastError}><CircleAlert size={15}/><span>{info.lastError}</span></div>}
    <div className="card-actions">
      <button className="button secondary slim" disabled={busy || !automationEnabled} title={!automationEnabled ? "Press Run automation first" : undefined} onClick={() => onAction(info.desired ? "stop" : "start", info.mode)}>{info.desired ? <Pause size={16}/> : <Play size={16}/>} {info.desired ? "Pause" : "Start"}</button>
      <button className="button secondary slim" disabled={busy || !automationEnabled} onClick={() => onAction("restart", info.mode)}><RotateCcw size={16}/> Restart</button>
      <span className="recovery"><Activity size={15}/>{info.restarts ? String(info.restarts) + " recovery attempts" : "Self-healing enabled"}</span>
    </div>
  </article>;
}

function Pairing({ paired, busy, onSave, onClose }: { paired: boolean; busy: boolean; onSave: (agentId: string, token: string) => void; onClose: () => void }) {
  const [agentId, setAgentId] = useState("");
  const [token, setToken] = useState("");
  return <div className="modal-layer" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="modal" role="dialog" aria-modal="true" aria-label="Scout pairing">
      <div className="modal-title"><div><h2>Scout pairing</h2><p>Credentials remain on this computer in scout.local.env.</p></div><button className="icon-button" onClick={onClose} aria-label="Close pairing"><X size={20}/></button></div>
      <form onSubmit={e => { e.preventDefault(); onSave(agentId.trim(), token.trim()); }}>
        <label>Agent ID<input maxLength={128} required value={agentId} onChange={e => setAgentId(e.target.value)} placeholder="Paste Agent ID"/></label>
        <label>{paired ? "New token (leave empty to preserve current pairing)" : "Scout token"}<input autoComplete="off" type="password" maxLength={1024} required={!paired} value={token} onChange={e => setToken(e.target.value)} placeholder="Token is stored locally"/></label>
        <div className="modal-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" type="submit" disabled={busy}><Check size={16}/> Save pairing</button></div>
      </form>
    </div>
  </div>;
}

export default function App() {
  const [state, setState] = useState<Dashboard>(EMPTY);
  const [mode, setMode] = useState<ModeName>("review");
  const [logs, setLogs] = useState("");
  const [keywordSummary, setKeywordSummary] = useState<KeywordSummary | null>(null);
  const [keywordError, setKeywordError] = useState<string | null>(null);
  const [operations, setOperations] = useState<OperationsSummary | null>(null);
  const [operationsError, setOperationsError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pairing, setPairing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const scrollRef = useRef<HTMLPreElement>(null);
  const refreshInFlight = useRef(false);
  const refresh = useCallback(async () => {
    if (!isNative || refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const [dashboard, tail] = await Promise.all([
        invoke<Dashboard>("dashboard"),
        invoke<string>("log_tail", { mode, maxLines: 80 }),
      ]);
      setState(dashboard);
      setLogs(tail);
    } finally {
      refreshInFlight.current = false;
    }
  }, [mode]);
  useEffect(() => {
    let alive = true;
    if (!isNative) { setToast("Browser preview — controls require the Windows desktop app."); return; }
    const poll = () => void refresh().catch(() => { if (alive) setToast("Unable to connect to native Scout Manager."); });
    poll();
    const interval = window.setInterval(poll, 2000);
    return () => { alive = false; window.clearInterval(interval); };
  }, [refresh]);
  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [logs, mode]);
  useEffect(() => {
    if (!isNative) return;
    let active = true;
    const update = () => void invoke<KeywordSummary>("keyword_summary").then(
      result => { if (active) { setKeywordSummary(result); setKeywordError(null); } },
      error => { if (active) setKeywordError(typeof error === "string" ? error : "Keyword statistics unavailable"); }
    );
    update();
    const interval = window.setInterval(update, 30000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);
  useEffect(() => {
    if (!isNative) return;
    let active = true;
    let inFlight = false;
    const update = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await invoke<OperationsSummary>("operations_summary");
        if (active) { setOperations(result); setOperationsError(null); }
      } catch (e) {
        if (active) {
          setOperations(null); // do not show stale counts as live data
          setOperationsError(typeof e === "string" ? e : "Scout job status temporarily unavailable");
        }
      } finally {
        inFlight = false;
      }
    };
    void update();
    const interval = window.setInterval(() => void update(), 15000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  const act = async (name: string, args: Record<string, unknown> = {}): Promise<boolean> => {
    if (!isNative) return false;
    setBusy(true); setToast(null);
    try { await invoke(name, args); await refresh(); return true; }
    catch (e) { setToast(typeof e === "string" ? e : e instanceof Error ? e.message : "Request failed"); return false; }
    finally { setBusy(false); }
  };
  const onMode = (command: "start" | "stop" | "restart", which: ModeName) => void act("control_scout", { mode: which, command });
  const review = state.modes.find(m => m.mode === "review") ?? EMPTY.modes[0];
  const keyword = state.modes.find(m => m.mode === "keyword") ?? EMPTY.modes[1];
  const updateProblem = /error|paused|fail/i.test(state.updateState);
  const controlBlocked = busy || !isNative || !state.controllerAvailable;
  return <main><div className="shell">
    <header className="top">
      <div className="heading"><div className="eyebrow">CREATIVE ASSET MANAGER / AUTOMATION</div><div className="product-title"><h1>RRUGC Scout Manager</h1><span className="manager-version" title="Installed Windows desktop app version">Manager v{state.managerVersion}</span></div><p>Automated scouting for Review + Keyword, with managed recovery.</p><div className="submeta"><span><Clock3 size={13}/> Updates only while automation runs</span><span className="tiny-separator"/><span>Scout <strong>{state.version}</strong></span><span className="tiny-separator"/><span>Commit <strong>{state.commit}</strong></span></div></div>
      <div className="toolbar">
        <button className="button primary" disabled={controlBlocked} onClick={() => void act("control_all", { command: "start" })}><CirclePlay size={18}/> Run automation</button>
        <button className="button secondary" disabled={controlBlocked || !state.automationEnabled} onClick={() => void act("control_all", { command: "stop" })}><CirclePause size={18}/> Pause</button>
        <button className="button secondary" disabled={controlBlocked} onClick={() => void act("check_update")}><RefreshCcw size={18}/> Update</button>
        <button className="button secondary" onClick={() => setPairing(true)} disabled={!isNative || !state.controllerAvailable}><Settings2 size={18}/> Pairing</button>
      </div>
    </header>
    <div className="summary">
      <div className="summary-item"><span className={"sum-icon " + (review.state === "Running" ? "ok" : "off")}><Check size={19}/></span><div><span className="sum-title">Review Scout</span><strong className={review.state === "Running" ? "ok-text" : ""}>{review.state}</strong></div></div>
      <div className="summary-item"><span className={"sum-icon " + (keyword.state === "Running" ? "ok" : "off")}><Check size={19}/></span><div><span className="sum-title">Keyword Scout</span><strong className={keyword.state === "Running" ? "ok-text" : ""}>{keyword.state}</strong></div></div>
      <div className="summary-item"><span className={"sum-icon " + (updateProblem ? "off" : "ok")}><RefreshCcw size={19}/></span><div><span className="sum-title">Auto update</span><strong className={updateProblem ? "warn-text" : ""}>{state.updateState}</strong></div></div>
      <div className="summary-item"><span className="sum-icon ok"><Search size={19}/></span><div><span className="sum-title">Stage 0 keywords</span><strong>{keywordSummary ? keywordSummary.total_keywords.toLocaleString("en-US") + " saved · +" + keywordSummary.added_24h + " / 24h" : keywordError ? "Stats unavailable" : "Checking server..."}</strong></div></div>
    </div>
    {!state.controllerAvailable && <div className="alert"><CircleAlert size={18}/><span>Another Scout Manager is running. Close the legacy Manager before using automation controls in Tauri.</span></div>}
    {toast && <div className="alert"><CircleAlert size={18}/><span>{toast}</span><button onClick={() => setToast(null)} aria-label="Dismiss alert"><X size={16}/></button></div>}
    <section className="mode-grid">
      <Card info={review} version={state.version} commit={state.commit} busy={controlBlocked} automationEnabled={state.automationEnabled} operations={operations} operationsError={operationsError} onAction={onMode}/>
      <Card info={keyword} version={state.version} commit={state.commit} busy={controlBlocked} automationEnabled={state.automationEnabled} keywordSummary={keywordSummary} keywordError={keywordError} operations={operations} operationsError={operationsError} onAction={onMode}/>
    </section>
    <section className="terminal-panel">
      <div className="terminal-header"><div className="terminal-title"><span className="terminal-icon"><Terminal size={19}/></span><div><strong>Live activity</strong><small>Latest output from the selected Scout</small></div></div><div className="terminal-controls"><label className="sr-only" htmlFor="logMode">Scout logs</label><select id="logMode" value={mode} onChange={e => setMode(e.target.value as ModeName)}><option value="review">Review Scout</option><option value="keyword">Keyword Scout</option></select><button className="log-button" onClick={() => { navigator.clipboard?.writeText(logs).then(() => setToast("Logs copied.")).catch(() => setToast("Clipboard unavailable.")); }} title="Copy visible logs"><Copy size={16}/> Copy</button></div></div>
      <pre className="log-pre" ref={scrollRef}>{logs || (isNative ? "Waiting for Scout activity…" : "Browser preview — logs appear in the Windows desktop application.")}</pre>
      <div className="terminal-footer"><span><span className="live-dot"/> Monitor refreshes every 2 seconds</span><span>Manager v{state.managerVersion} · Scout {state.version}</span></div>
    </section>
    <footer className="foot"><span><ShieldCheck size={14}/> Local credentials • Separate Chrome profiles • Bounded retries</span><span>{state.paired ? "Paired" : "Pairing required"} · Close window to tray · <button type="button" className="quit-link" disabled={busy || !isNative} onClick={() => { if (window.confirm("Quit Scout Manager and stop both Scouts?")) void act("quit_manager"); }}>Quit and stop</button></span></footer>
  </div>
  {pairing && <Pairing paired={state.paired} busy={busy} onClose={() => setPairing(false)} onSave={(agentId, token) => { void (async () => { if (await act("save_pairing", { agentId, token })) { setPairing(false); setToast("Pairing saved. Press Run automation to start."); } })(); }}/>}
  </main>;
}
