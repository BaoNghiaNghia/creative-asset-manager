import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Activity, Check, CircleAlert, CirclePause, CirclePlay, Clock3, Copy, FileText, LockKeyhole, Pause, Play, RefreshCcw, RotateCcw, Search, Settings2, ShieldCheck, Terminal, X } from "lucide-react";

type ModeName = "review" | "keyword";
type ModeInfo = { mode: ModeName; state: string; pid: number | null; desired: boolean; restarts: number; lastError: string | null };
type Dashboard = { version: string; commit: string; updateState: string; paired: boolean; updating: boolean; modes: ModeInfo[] };
const EMPTY: Dashboard = { version: "rrugc-scout-v45", commit: "—", updateState: "Connecting to runtime", paired: false, updating: false, modes: [
  { mode: "review", state: "Stopped", pid: null, desired: false, restarts: 0, lastError: null },
  { mode: "keyword", state: "Stopped", pid: null, desired: false, restarts: 0, lastError: null },
] };
const isNative = typeof window !== "undefined" && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);

function Card({ info, version, commit, busy, onAction }: {
  info: ModeInfo; version: string; commit: string; busy: boolean;
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
    <div className="meta-row"><div><span>PID</span><b>{info.pid ?? "—"}</b></div><div><span>Version</span><b>{version}</b></div><div><span>Commit</span><b>{commit}</b></div></div>
    <div className="chips"><div><RefreshCcw/><span>Auto restart</span></div><div><ShieldCheck/><span>Browser watchdog</span></div><div><LockKeyhole/><span>Isolated profile</span></div></div>
    {info.lastError && <div className="inline-error" title={info.lastError}><CircleAlert size={15}/><span>{info.lastError}</span></div>}
    <div className="card-actions">
      <button className="button secondary slim" disabled={busy} onClick={() => onAction(info.desired ? "stop" : "start", info.mode)}>{info.desired ? <Pause size={16}/> : <Play size={16}/>} {info.desired ? "Pause" : "Start"}</button>
      <button className="button secondary slim" disabled={busy} onClick={() => onAction("restart", info.mode)}><RotateCcw size={16}/> Restart</button>
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
  const [busy, setBusy] = useState(false);
  const [pairing, setPairing] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const scrollRef = useRef<HTMLPreElement>(null);
  const refresh = useCallback(async () => {
    if (!isNative) return;
    const dashboard = await invoke<Dashboard>("dashboard");
    setState(dashboard);
    setLogs(await invoke<string>("log_tail", { mode, maxLines: 80 }));
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
  const running = state.modes.filter(m => m.state === "Running").length;
  const updateProblem = /error|paused|fail/i.test(state.updateState);
  return <main><div className="shell">
    <header className="top">
      <div className="heading"><div className="eyebrow">CREATIVE ASSET MANAGER / AUTOMATION</div><h1>RRUGC Scout Manager</h1><p>Automated scouting for Review + Keyword, with managed recovery.</p><div className="submeta"><span><Clock3 size={13}/> Automatic update checks</span><span className="tiny-separator"/><span>Commit <strong>{state.commit}</strong></span></div></div>
      <div className="toolbar">
        <button className="button primary" disabled={busy || !isNative} onClick={() => void act("control_all", { command: "start" })}><CirclePlay size={18}/> Run automation</button>
        <button className="button secondary" disabled={busy || !isNative} onClick={() => void act("control_all", { command: "stop" })}><CirclePause size={18}/> Pause</button>
        <button className="button secondary" disabled={busy || !isNative} onClick={() => void act("check_update")}><RefreshCcw size={18}/> Update</button>
        <button className="button secondary" onClick={() => setPairing(true)} disabled={!isNative}><Settings2 size={18}/> Pairing</button>
      </div>
    </header>
    <div className="summary">
      <div className="summary-item"><span className={"sum-icon " + (review.state === "Running" ? "ok" : "off")}><Check size={19}/></span><div><span className="sum-title">Review Scout</span><strong className={review.state === "Running" ? "ok-text" : ""}>{review.state}</strong></div></div>
      <div className="summary-item"><span className={"sum-icon " + (keyword.state === "Running" ? "ok" : "off")}><Check size={19}/></span><div><span className="sum-title">Keyword Scout</span><strong className={keyword.state === "Running" ? "ok-text" : ""}>{keyword.state}</strong></div></div>
      <div className="summary-item"><span className={"sum-icon " + (updateProblem ? "off" : "ok")}><RefreshCcw size={19}/></span><div><span className="sum-title">Auto update</span><strong className={updateProblem ? "warn-text" : ""}>{state.updateState}</strong></div></div>
      <div className="summary-item"><span className="sum-icon ok"><ShieldCheck size={19}/></span><div><span className="sum-title">System protection</span><strong>{running === 2 ? "Both scouts running" : "Auto recovery enabled"}</strong></div></div>
    </div>
    {toast && <div className="alert"><CircleAlert size={18}/><span>{toast}</span><button onClick={() => setToast(null)} aria-label="Dismiss alert"><X size={16}/></button></div>}
    <section className="mode-grid">
      <Card info={review} version={state.version} commit={state.commit} busy={busy} onAction={onMode}/>
      <Card info={keyword} version={state.version} commit={state.commit} busy={busy} onAction={onMode}/>
    </section>
    <section className="terminal-panel">
      <div className="terminal-header"><div className="terminal-title"><span className="terminal-icon"><Terminal size={19}/></span><div><strong>Live activity</strong><small>Latest output from the selected Scout</small></div></div><div className="terminal-controls"><label className="sr-only" htmlFor="logMode">Scout logs</label><select id="logMode" value={mode} onChange={e => setMode(e.target.value as ModeName)}><option value="review">Review Scout</option><option value="keyword">Keyword Scout</option></select><button className="log-button" onClick={() => { navigator.clipboard?.writeText(logs).then(() => setToast("Logs copied.")).catch(() => setToast("Clipboard unavailable.")); }} title="Copy visible logs"><Copy size={16}/> Copy</button></div></div>
      <pre className="log-pre" ref={scrollRef}>{logs || (isNative ? "Waiting for Scout activity…" : "Browser preview — logs appear in the Windows desktop application.")}</pre>
      <div className="terminal-footer"><span><span className="live-dot"/> Monitor refreshes every 2 seconds</span><span>Scout {state.version}</span></div>
    </section>
    <footer className="foot"><span><ShieldCheck size={14}/> Local credentials • Separate Chrome profiles • Bounded retries</span><span>{state.paired ? "Paired" : "Pairing required"} · Minimize to tray to keep working</span></footer>
  </div>
  {pairing && <Pairing paired={state.paired} busy={busy} onClose={() => setPairing(false)} onSave={(agentId, token) => { void (async () => { if (await act("save_pairing", { agentId, token })) { setPairing(false); await act("control_all", { command: "start" }); } })(); }}/>}
  </main>;
}
