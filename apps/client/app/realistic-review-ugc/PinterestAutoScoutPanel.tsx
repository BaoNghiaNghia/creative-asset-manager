import { useEffect, useMemo, useState } from "react";
import {
  createScoutAgent,
  listScoutAgents,
  listScoutRuns,
  listScoutMetrics,
  resetScoutAgentPairing,
} from "./api";
import type { ScoutAgent, ScoutAgentCreated, ScoutRun } from "./types";
import { WorkflowStatusIcon } from "./WorkflowStatusIcon";

const time = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "Never";

export function scoutMachineStatus(agent: ScoutAgent | undefined) {
  if (!agent) return { tone: "unknown", label: "Unlinked", icon: "alert" as const };
  if (agent.status === "offline") return { tone: "offline", label: "Offline", icon: "wifi-off" as const };
  if (agent.status === "error") return { tone: "error", label: "Error", icon: "alert" as const };
  if (agent.status === "needs_login") return { tone: "warning", label: "Login needed", icon: "alert" as const };
  return { tone: "online", label: "Online", icon: "wifi" as const };
}

const DEFAULT_PROFILE_DIR =
  "D:\\Bot_Tool_Auto_Game\\scan_pinterest\\pinterest-profile";

export const MIN_SCOUT_CLIENT_VERSION = 48;

export function scoutClientIsCurrent(value: string | null | undefined): boolean {
  const match = /^rrugc-scout-v(\d+)$/.exec((value || "").trim());
  return Boolean(match && Number(match[1]) >= MIN_SCOUT_CLIENT_VERSION);
}

export function autoScoutCommand(
  baseUrl: string,
  agentId: string,
  token: string,
  profileDir = "./.rrugc-pinterest-profile",
): string {
  const url = baseUrl.replace(/\/$/, "");
  return [
    "python apps/rrugc_scout/scout.py",
    "--base-url \"" + url + "\"",
    "--agent-id \"" + agentId + "\"",
    "--token \"" + token + "\"",
    "--profile-dir \"" + profileDir + "\"",
  ].join(" ");
}

export function autoScoutBootstrapCommand(
  profileDir = "./.rrugc-pinterest-profile",
): string {
  return [
    "python apps/rrugc_scout/scout.py",
    "--profile-dir \"" + profileDir + "\"",
    "--bootstrap-login",
  ].join(" ");
}

export function scoutLocalConfig(
  baseUrl: string,
  agentId: string,
  token: string,
  profileDir = DEFAULT_PROFILE_DIR,
): string {
  return [
    "RRUGC_BASE_URL=" + baseUrl.replace(/\/$/, ""),
    "RRUGC_AGENT_ID=" + agentId,
    "RRUGC_SCOUT_TOKEN=" + token,
    "RRUGC_PROFILE_DIR=" + profileDir,
    "RRUGC_PACE=careful",
    "RRUGC_DETAIL_CONCURRENCY=1",
  ].join("\n");
}

export function PinterestAutoScoutPanel({
  onError,
}: {
  onError: (message: string) => void;
}) {
  const [agents, setAgents] = useState<ScoutAgent[]>([]);
  const [runs, setRuns] = useState<ScoutRun[]>([]);
  const [metrics, setMetrics] = useState<Awaited<ReturnType<typeof listScoutMetrics>> | null>(null);
  const [created, setCreated] = useState<ScoutAgentCreated | null>(null);
  const [name, setName] = useState("Pinterest Auto Scout");
  const [profileDir, setProfileDir] = useState(DEFAULT_PROFILE_DIR);
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState<"agent-id" | "token" | "bootstrap" | "agent" | "">("");

  const onlineAgents = agents.filter(agent => agent.status === "ready" || agent.status === "busy");
  const isOnline = onlineAgents.length > 0;
  const outdatedAgents = onlineAgents.filter(agent => !scoutClientIsCurrent(agent.client_version));
  const loginRequiredAgents = onlineAgents.filter(agent => agent.status === "needs_login");
  const errorAgents = onlineAgents.filter(agent => agent.status === "error");

  const bootstrapCommand = useMemo(
    () => autoScoutBootstrapCommand(profileDir.trim() || "./.rrugc-pinterest-profile"),
    [profileDir],
  );
  const command = useMemo(
    () => created
      ? autoScoutCommand(
          window.location.origin,
          created.id,
          created.agent_token,
          profileDir.trim() || DEFAULT_PROFILE_DIR,
        )
      : "",
    [created, profileDir],
  );

  async function refresh(signal?: AbortSignal) {
    const [agentRows, runRows, metricRows] = await Promise.all([
      listScoutAgents(signal),
      listScoutRuns(undefined, signal),
      listScoutMetrics(signal),
    ]);
    setAgents(agentRows);
    setRuns(runRows);
    setMetrics(metricRows);
  }

  useEffect(() => {
    const savedProfileDir = window.localStorage.getItem("rrugc:pinterest-profile-dir");
    if (savedProfileDir?.trim()) setProfileDir(savedProfileDir.trim());

    const controller = new AbortController();
    void refresh(controller.signal).catch(reason => {
      if (!controller.signal.aborted) {
        onError(reason instanceof Error ? reason.message : "Unable to load Pinterest Auto Scout.");
      }
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh().catch(() => undefined);
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  async function pairAgent() {
    const nextName = name.trim();
    if (busy || !nextName) return;
    setBusy("pair");
    setCopied("");
    onError("");
    try {
      const next = await createScoutAgent(nextName);
      setCreated(next);
      setName("Pinterest Auto Scout");
      await refresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to pair Pinterest Auto Scout.");
    } finally {
      setBusy("");
    }
  }

  async function resetAgent(agent: ScoutAgent) {
    if (busy) return;
    setBusy("reset:" + agent.id);
    setCopied("");
    onError("");
    try {
      const next = await resetScoutAgentPairing(agent.id);
      setCreated(next);
      await refresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to reset Scout pairing.");
    } finally {
      setBusy("");
    }
  }

  async function copy(value: string, kind: "agent-id" | "token" | "bootstrap" | "agent") {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setCopied(kind);
  }

  const activeRun = runs.find(row => row.status === "claimed" || row.status === "running");
  const lastRun = runs[0] || null;
  const recentRuns = runs.slice(0, 20);
  const completedRuns = recentRuns.filter(row => row.status === "completed");
  const submitted = completedRuns.reduce((sum, row) => sum + row.submitted_count, 0);
  const createdCount = completedRuns.reduce((sum, row) => sum + row.created_count, 0);
  const duplicateCount = completedRuns.reduce((sum, row) => sum + row.existing_count, 0);
  const yieldRate = submitted > 0 ? Math.round((createdCount / submitted) * 100) : null;
  const duplicateRate = submitted > 0 ? Math.round((duplicateCount / submitted) * 100) : null;
  const heartbeatAgeMs = activeRun?.last_heartbeat_at
    ? Date.now() - new Date(activeRun.last_heartbeat_at).getTime()
    : null;
  const isStalled = Boolean(activeRun && heartbeatAgeMs !== null && heartbeatAgeMs > 120_000);
  const recentFailures = recentRuns.filter(row => row.status === "failed" || row.status === "cancelled").length;
  const keywordMachines = metrics?.items.filter(item => item.mode === "keyword") || [];
  const reviewMachines = metrics?.review_items || [];
  const keywordAgentsWithoutMetrics = agents.filter(agent => !keywordMachines.some(item => item.agent_id === agent.id));
  const health = agents.length === 0
    ? { tone: "is-offline", label: "No Scout paired", detail: "Pair each Scout machine once, then run its launcher." }
    : !isOnline
      ? { tone: "is-offline", label: "Scouts offline", detail: "Run START_SCOUT.bat on the Scout machines; they will update and reconnect automatically." }
      : outdatedAgents.length > 0
        ? {
            tone: "is-warning",
            label: "Scout update recommended",
            detail: outdatedAgents.length + " online Scout(s) are below rrugc-scout-v" + MIN_SCOUT_CLIENT_VERSION + ". Run START_SCOUT.bat on those machines.",
          }
        : isStalled
          ? { tone: "is-warning", label: "Scan stalled", detail: "No run heartbeat for more than 2 minutes. The lease watchdog will release it automatically." }
          : loginRequiredAgents.length > 0
            ? { tone: "is-warning", label: "Login required", detail: loginRequiredAgents.length + " Scout machine(s) need Pinterest login." }
            : errorAgents.length > 0 || recentFailures >= 3
              ? { tone: "is-warning", label: "Recovery needed", detail: "One or more Scout machines are failing; check the machine rows below." }
              : { tone: "is-online", label: activeRun ? "Scanning normally" : "Pipeline healthy", detail: activeRun ? "Scout heartbeats are current and campaign leases are active." : "Online Scouts are ready for the next campaign." };

  return <section className="rrugc-card rrugc-auto-scout-panel" aria-label="Pinterest Auto Scout">
    <div className="rrugc-section-heading rrugc-auto-scout-heading">
      <div>
        <small>SCOUT OPERATIONS</small>
        <h2><WorkflowStatusIcon name="monitor" size={19} />Auto Scout</h2>
        <p>Machine connections, keyword discovery and reference performance · last 7 days</p>
      </div>
      <span className={"rrugc-auto-scout-health " + health.tone} title={health.detail}>
        <WorkflowStatusIcon name={health.tone === "is-online" ? "shield-check" : health.tone === "is-offline" ? "wifi-off" : "alert"} size={14} />
        {health.label}
      </span>
    </div>

    <div className="rrugc-scout-overview" role="status" aria-label="Scout connections overview">
      <span><small><WorkflowStatusIcon name="monitor" size={13} /> Paired machines</small><b>{agents.length.toLocaleString()}</b></span>
      <span className={onlineAgents.length < agents.length ? "is-warning" : "is-good"}><small><WorkflowStatusIcon name="wifi" size={13} /> Online</small><b>{onlineAgents.length}<em> / {agents.length}</em></b></span>
      <span><small><WorkflowStatusIcon name={activeRun ? "activity" : "circle-pause"} size={13} /> Discovery</small><b>{activeRun ? "Scanning" : "Idle"}</b></span>
      <span><small><WorkflowStatusIcon name="image" size={13} /> New references</small><b>{createdCount.toLocaleString()}</b></span>
    </div>

    <div className="rrugc-scout-summary-heading">
      <strong><WorkflowStatusIcon name="key" size={15} /> Keyword discovery</strong>
      <small>All machines · tenant-wide</small>
    </div>
    <div className="rrugc-scout-keyword-summary" role="status" aria-label="Tenant-wide Keyword Scout counters">
      <span><small><WorkflowStatusIcon name="key" size={12} /> Total keywords</small><b>{metrics?.overview.total_keywords.toLocaleString() ?? "—"}</b></span>
      <span><small><WorkflowStatusIcon name="sparkles" size={12} /> New · 24h</small><b>{metrics?.overview.added_24h.toLocaleString() ?? "—"}</b></span>
      <span><small><WorkflowStatusIcon name="trending-up" size={12} /> New · 7d</small><b>{metrics?.overview.added_7d.toLocaleString() ?? "—"}</b></span>
      <span><small><WorkflowStatusIcon name="clock" size={12} /> Priority pending</small><b>{metrics?.overview.priority_pending.toLocaleString() ?? "—"}</b></span>
      <span className="is-positive"><small><WorkflowStatusIcon name="thumbs-up" size={12} /> Suggested</small><b>{metrics ? (metrics.feedback.suggested || 0).toLocaleString() : "—"}</b></span>
      <span className="is-negative"><small><WorkflowStatusIcon name="thumbs-down" size={12} /> Blocked</small><b>{metrics ? (metrics.feedback.blocked || 0).toLocaleString() : "—"}</b></span>
    </div>

    {agents.length > 0 && <div className="rrugc-scout-machine-sections" aria-label="Scout metrics by machine">
      <section className="rrugc-scout-machine-section" aria-label="Keyword Scout machines">
        <div className="rrugc-scout-machine-heading">
          <strong><span className="rrugc-scout-category-icon is-keyword"><WorkflowStatusIcon name="key" size={16} /></span> Keyword Scout</strong>
          <small>{keywordMachines.length} reporting · Last 7 days</small>
        </div>
        <div className="rrugc-scout-machine-cards">
          {keywordMachines.map(item => {
            const agent = agents.find(row => row.id === item.agent_id);
            const status = scoutMachineStatus(agent);
            return <article key={item.agent_id + ":" + item.machine_label} className="rrugc-scout-machine-card is-keyword">
              <header>
                <div className="rrugc-scout-machine-title"><span className="rrugc-scout-machine-avatar"><WorkflowStatusIcon name="monitor" size={16} /></span><strong title={item.machine_label}>{item.machine_label}</strong></div>
                <span className={"rrugc-scout-machine-status is-" + status.tone}><WorkflowStatusIcon name={status.icon} size={12} />{status.label}</span>
              </header>
              <div className="rrugc-scout-machine-counts">
                <span><small>Pins scanned</small><b>{item.scanned_pins.toLocaleString()}</b></span>
                <span><small>Keywords found</small><b>{item.found_quotes.toLocaleString()}</b></span>
                <span><small>New keywords</small><b>{item.new_keywords.toLocaleString()}</b></span>
                <span><small>Duplicates</small><b>{item.duplicate_pins.toLocaleString()}</b></span>
                <span className={item.errors > 0 ? "is-error" : ""}><small>Errors</small><b>{item.errors.toLocaleString()}</b></span>
              </div>
              <footer><WorkflowStatusIcon name="clock" size={12} /><span>Last report: {time(item.last_activity_at)}</span></footer>
            </article>;
          })}
          {keywordAgentsWithoutMetrics.map(agent => {
            const status = scoutMachineStatus(agent);
            return <article key={agent.id} className="rrugc-scout-machine-card is-keyword is-empty">
              <header>
                <div className="rrugc-scout-machine-title"><span className="rrugc-scout-machine-avatar"><WorkflowStatusIcon name="monitor" size={16} /></span><strong title={agent.machine_label || agent.name}>{agent.machine_label || agent.name}</strong></div>
                <span className={"rrugc-scout-machine-status is-" + status.tone}><WorkflowStatusIcon name={status.icon} size={12} />{status.label}</span>
              </header>
              <p className="rrugc-scout-metrics-empty">No keyword cycles reported yet. Check the Keyword Scout process on this machine.</p>
              <footer><WorkflowStatusIcon name="clock" size={12} /><span>Last seen: {time(agent.last_seen_at)}</span></footer>
            </article>;
          })}
        </div>
      </section>

      <section className="rrugc-scout-machine-section" aria-label="Review Scout machines">
        <div className="rrugc-scout-machine-heading">
          <strong><span className="rrugc-scout-category-icon is-review"><WorkflowStatusIcon name="image" size={16} /></span> Review Scout</strong>
          <small>{reviewMachines.length} reporting · Last 7 days</small>
        </div>
        <div className="rrugc-scout-machine-cards">
          {reviewMachines.map(item => {
            const agent = agents.find(row => row.id === item.agent_id);
            const status = scoutMachineStatus(agent);
            const machineName = agent?.machine_label || agent?.name || item.agent_id;
            return <article key={"review:" + item.agent_id} className="rrugc-scout-machine-card is-review">
              <header>
                <div className="rrugc-scout-machine-title"><span className="rrugc-scout-machine-avatar"><WorkflowStatusIcon name="monitor" size={16} /></span><strong title={machineName}>{machineName}</strong></div>
                <span className={"rrugc-scout-machine-status is-" + status.tone}><WorkflowStatusIcon name={status.icon} size={12} />{status.label}</span>
              </header>
              <div className="rrugc-scout-machine-counts">
                <span><small>Submitted</small><b>{item.submitted.toLocaleString()}</b></span>
                <span><small>New references</small><b>{item.new_references.toLocaleString()}</b></span>
                <span><small>Duplicates</small><b>{item.duplicates.toLocaleString()}</b></span>
                <span><small>Runs</small><b>{item.runs.toLocaleString()}</b></span>
                <span className={item.failed_runs > 0 ? "is-error" : ""}><small>Failed runs</small><b>{item.failed_runs.toLocaleString()}</b></span>
              </div>
              <footer><WorkflowStatusIcon name="clock" size={12} /><span>Last run: {time(item.last_activity_at)}</span></footer>
            </article>;
          })}
          {reviewMachines.length === 0 && <div className="rrugc-scout-machine-empty"><WorkflowStatusIcon name="image" size={16} />No Review Scout reports in this period.</div>}
        </div>
      </section>
    </div>}

    {(health.tone !== "is-online" || activeRun) && <div className={"rrugc-scout-health-note " + health.tone}>
      <strong>{health.label}</strong>
      <span>{health.detail}</span>
      <div>
        <small>Yield <b>{yieldRate === null ? "—" : yieldRate + "%"}</b></small>
        <small>Duplicates <b>{duplicateRate === null ? "—" : duplicateRate + "%"}</b></small>
        <small>Failures <b>{recentFailures}</b></small>
      </div>
    </div>}

    <details className="rrugc-compact-disclosure rrugc-scout-disclosure" open={created ? true : undefined}>
      <summary>
        <span>
          <strong>Setup & diagnostics</strong>
          <small>{agents.length ? agents.length + " Scout machine(s) paired" : "Pair each Scout machine once"}</small>
        </span>
        <b>{created ? "Pairing ready" : agents.length ? "Manage" : "Setup"}</b>
      </summary>

      <div className="rrugc-scout-manage-grid">
        <div className="rrugc-scout-agent-list">
          {agents.length ? agents.map(agent => <div className="rrugc-scout-connected-row" key={agent.id}>
            <div>
              <strong>{agent.machine_label || agent.name}</strong>
              <small>
                {agent.machine_label ? agent.name + " · " : ""}
                {agent.status.replaceAll("_", " ")}
                {" · "}{agent.client_version || "client not connected"}
                {" · Last seen "}{time(agent.last_seen_at)}
              </small>
              {agent.last_error_code && <em>{agent.last_error_code.replaceAll("_", " ")}</em>}
            </div>
            <button
              type="button"
              className="rrugc-scout-reset"
              disabled={Boolean(busy)}
              onClick={() => void resetAgent(agent)}
            >
              {busy === "reset:" + agent.id ? "Resetting…" : "Reset pairing"}
            </button>
          </div>) : <div className="rrugc-scout-empty">
            <strong>No Scout machine paired</strong>
            <small>Add the first machine below. Each machine receives its own Agent ID and token.</small>
          </div>}
        </div>

        <div className="rrugc-scout-setup-row">
          <label>
            <span>Add Scout machine</span>
            <input
              value={name}
              maxLength={160}
              placeholder="Example: DESKTOP-91TD9B5"
              onChange={event => setName(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="rrugc-primary"
            disabled={Boolean(busy) || !name.trim()}
            onClick={() => void pairAgent()}
          >
            {busy === "pair" ? "Pairing…" : "Add Scout"}
          </button>
        </div>

        {lastRun && <div className="rrugc-scout-run-summary">
          <span><small>Latest run</small><b>{lastRun.status.replaceAll("_", " ")}</b></span>
          <span><small>Submitted</small><b>{lastRun.submitted_count}</b></span>
          <span><small>New</small><b>{lastRun.created_count}</b></span>
          <span><small>Started</small><b>{time(lastRun.started_at)}</b></span>
        </div>}

        {created && <div className="rrugc-pairing-ready">
          <div>
            <strong>Pairing ready</strong>
            <small>Paste these once when START_SCOUT.bat asks for them.</small>
          </div>
          <div>
            <button type="button" onClick={() => void copy(created.id, "agent-id")}>
              {copied === "agent-id" ? "Agent ID copied" : "Copy Agent ID"}
            </button>
            <button type="button" className="rrugc-primary" onClick={() => void copy(created.agent_token, "token")}>
              {copied === "token" ? "Token copied" : "Copy token"}
            </button>
          </div>
        </div>}

        <details className="rrugc-scout-advanced">
          <summary>
            <span>
              <strong>Advanced</strong>
              <small>Manual login or troubleshooting only</small>
            </span>
            <b>Fallback</b>
          </summary>
          <div className="rrugc-scout-advanced-body">
            <label className="rrugc-auto-scout-profile-field">
              <span>Persistent profile directory</span>
              <input
                value={profileDir}
                maxLength={500}
                placeholder={DEFAULT_PROFILE_DIR}
                onChange={event => {
                  const next = event.target.value;
                  setProfileDir(next);
                  window.localStorage.setItem("rrugc:pinterest-profile-dir", next);
                }}
              />
            </label>
            <div className="rrugc-token-actions">
              <button type="button" onClick={() => void copy(bootstrapCommand, "bootstrap")}>
                {copied === "bootstrap" ? "Login command copied" : "Copy login command"}
              </button>
              {created && <button type="button" onClick={() => void copy(command, "agent")}>
                {copied === "agent" ? "Manual command copied" : "Copy manual Scout command"}
              </button>}
            </div>
          </div>
        </details>
      </div>
    </details>
  </section>;
}