import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { spyFetch } from "./api";
import "./MuseMonitor.css";

type Event = {eventType: string; occurredAtUtc: string; receivedAtUtc: string; traceId: string | null; payload: Record<string, unknown>};
type Worker = {workerId: string; status: string; heartbeatAgeSeconds: number | null; apiActivityAgeSeconds: number | null; lastHeartbeat?: Event | null; lastApiActivity?: Event | null; lastError?: Event | null; cooldown?: {cooldownActive: boolean; secondsRemaining?: number}; executionReadiness?: {ready: boolean; reason?: string | null}};
type Counts = {byStatus: Record<string, number>; expiredClaims: number; missingLeaseExpiry: number};
type Report = {
  bridgeVersion: string; asOfUtc: string; videoAspectPhase: string;
  workers: Worker[]; commands: Counts; jobs: Counts;
  queueRecovery: {enabled: boolean; status: string; lastSweepAgeSeconds: number | null; lastSweep: Event | null};
  rendererCircuit?: {state: string; retryAfterSeconds: number; retryAtUtc?: string | null};
  recentEvents: Event[]; alerts: {id: string; severity: string; message: string}[];
};
const labels: Record<string, string> = {
  HEARTBEAT_RECENT: "Heartbeat OK",
  API_ACTIVE_HEARTBEAT_UNCONFIRMED: "API active · heartbeat unconfirmed",
  NO_RECENT_SIGNAL: "No recent signal",
  NOT_OBSERVED: "Not observed",
  RECENT: "Running", STALE: "Stale", LAST_SWEEP_FAILED: "Last sweep failed", DISABLED: "Disabled",
};
const age = (seconds: number | null | undefined) => seconds == null ? "—" :
  seconds < 60 ? seconds + "s ago" : seconds < 3600 ? Math.floor(seconds / 60) + "m ago" :
  Math.floor(seconds / 3600) + "h ago";
const field = (e: Event | null | undefined, key: string) => {
  const value = e?.payload?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
};
const dateText = (value: string) => {
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? "—" : d.toLocaleString("en-GB", {timeZone:"Asia/Ho_Chi_Minh", hour12:false});
};
const errorEvent = (e: Event) => Boolean(e.payload.errorCode) && e.payload.errorCode !== "NONE" || e.eventType === "CAM_RECOVERY_FAILED";
const tone = (status: string) => ["HEARTBEAT_RECENT", "RECENT", "closed"].includes(status) ? "good" : ["NO_RECENT_SIGNAL", "LAST_SWEEP_FAILED", "STALE"].includes(status) ? "bad" : "warn";

export function MuseMonitor() {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [worker, setWorker] = useState("all");
  const [errorsOnly, setErrorsOnly] = useState(false);
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      setBusy(true);
      try {
        const report = await spyFetch<Report>("/muse-monitor", {signal:abort.signal});
        if (!abort.signal.aborted) { setData(report); setError(""); }
      } catch {
        if (!abort.signal.aborted) setError("Could not refresh CAM diagnostics. Last snapshot, if any, is not live.");
      } finally {
        if (!abort.signal.aborted) { setBusy(false); timer = setTimeout(load, 15000); }
      }
    };
    void load();
    return () => { abort.abort(); if (timer) clearTimeout(timer); };
  }, [open, refresh]);
  function show() {
    setOpen(true); setError(""); setData(null); setWorker("all"); setErrorsOnly(false);
    dialog.current?.showModal();
  }
  function closed() { setOpen(false); setBusy(false); trigger.current?.focus(); }
  const events = data?.recentEvents.filter(e => (worker === "all" || e.payload.workerId === worker) && (!errorsOnly || errorEvent(e))) ?? [];
  return <>
    <button type="button" className="cam-muse-monitor-trigger" ref={trigger} onClick={show}>Muse Monitor</button>
    {createPortal(<dialog ref={dialog} onClose={closed} className="cam-muse-monitor" aria-labelledby="cam-muse-monitor-title">
      <header className="cam-muse-monitor-head">
        <div><h2 id="cam-muse-monitor-title">CAM · Muse Monitor</h2>
          <small>{data ? "Bridge " + data.bridgeVersion + " · " + dateText(data.asOfUtc) + " GMT+7" : "Live diagnostics · read only"}</small>
        </div>
        <div className="cam-muse-actions">
          <button type="button" onClick={() => setRefresh(x => x + 1)} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
          <button type="button" aria-label="Close Muse Monitor" onClick={() => dialog.current?.close()}>×</button>
        </div>
      </header>
      <div className="cam-muse-monitor-body">
        {error && <p role="alert" className="cam-muse-notice bad">{error}</p>}
        {!data && !error && <p role="status">Loading CAM diagnostics…</p>}
        {data && <>
          <div className="cam-muse-metrics">
            <div><small>Queue recovery</small><strong className={tone(data.queueRecovery.status)}>{labels[data.queueRecovery.status] ?? data.queueRecovery.status}</strong><small>{age(data.queueRecovery.lastSweepAgeSeconds)}</small></div>
            <div><small>Renderer circuit</small><strong className={tone(data.rendererCircuit?.state ?? "")}>{data.rendererCircuit?.state ?? "Unknown"}</strong><small>{data.rendererCircuit?.retryAfterSeconds ? "Retry in " + data.rendererCircuit.retryAfterSeconds + "s" : "CAM-side policy"}</small></div>
            <div><small>Video commands</small><strong>{data.commands.byStatus.receipt_submitted ?? 0} receipts</strong><small>{data.commands.byStatus.pending ?? 0} pending · {data.commands.byStatus.claimed ?? 0} claimed</small></div>
            <div><small>Video jobs</small><strong>{data.jobs.byStatus.qa_hold ?? 0} QA hold</strong><small>{data.videoAspectPhase === "vertical_first" ? "9:16 first" : data.videoAspectPhase} · {data.jobs.expiredClaims} expired</small></div>
          </div>
          <h3>Workers</h3>
          <div className="cam-muse-workers">
            {data.workers.map(w => <article key={w.workerId}>
              <div className="cam-muse-worker-head"><strong>{w.workerId}</strong><span className={tone(w.status)}>{labels[w.status] ?? w.status}</span></div>
              <dl>
                <dt>Last API</dt><dd>{age(w.apiActivityAgeSeconds)}</dd>
                <dt>Heartbeat V2</dt><dd>{age(w.heartbeatAgeSeconds)}</dd>
                <dt>Execution</dt><dd>{w.executionReadiness?.ready === false ? "Standby / not cleared" : w.executionReadiness?.ready === true ? "Allowed" : "Unconfirmed"}</dd>
                <dt>Cooldown</dt><dd>{w.cooldown?.cooldownActive ? String(w.cooldown.secondsRemaining ?? "—") + "s" : w.cooldown ? "No active cooldown" : "Unknown"}</dd>
                <dt>Last error</dt><dd>{w.lastError ? field(w.lastError,"errorCode") + " · " + dateText(w.lastError.occurredAtUtc) : "None observed"}</dd>
              </dl>
            </article>)}
          </div>
          <h3>Active diagnostics</h3>
          {data.alerts.length ? <div className="cam-muse-alerts">{data.alerts.map(a => <div key={a.id} className={a.severity === "error" ? "bad" : a.severity === "warning" ? "warn" : ""}><span>{a.severity.toUpperCase()}</span> {a.message}</div>)}</div> : <p>No active alerts from available CAM evidence.</p>}
          <div className="cam-muse-timeline-head">
            <h3>Recent events <small>(last {data.recentEvents.length})</small></h3>
            <div className="cam-muse-actions">
              <select aria-label="Filter Muse worker" value={worker} onChange={e => setWorker(e.target.value)}><option value="all">All sources</option>{data.workers.map(w => <option key={w.workerId} value={w.workerId}>{w.workerId}</option>)}</select>
              <label><input type="checkbox" checked={errorsOnly} onChange={e=>setErrorsOnly(e.target.checked)}/> Errors only</label>
            </div>
          </div>
          <div className="cam-muse-table-wrap"><table><thead><tr><th>Time · GMT+7</th><th>Source</th><th>Event / Route</th><th>Listing</th><th>HTTP / Outcome</th><th>Error</th></tr></thead>
            <tbody>{events.map((e,i)=><tr key={(e.traceId ?? e.receivedAtUtc)+":"+i}><td>{dateText(e.occurredAtUtc)}</td><td>{field(e,"workerId") === "—" ? "CAM" : field(e,"workerId")}</td><td>{e.eventType}<small>{field(e,"route")}</small></td><td>{field(e,"listingId")}</td><td>{field(e,"httpStatus")} / {field(e,"outcome")}</td><td className={errorEvent(e) ? "bad" : ""}>{field(e,"errorCode")}</td></tr>)}
            {!events.length && <tr><td colSpan={6}>No matching recent events.</td></tr>}</tbody></table></div>
          <p className="cam-muse-foot">CAM API activity is not proof of a live renderer or successful output. Alerts here are read-only; no manual retry or lease release. Refreshes every 15 seconds while open.</p>
        </>}
      </div>
    </dialog>, document.body)}
  </>;
}
