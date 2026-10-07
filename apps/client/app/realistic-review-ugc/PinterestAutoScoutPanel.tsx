import { useEffect, useMemo, useState } from "react";
import {
  createScoutAgent,
  listScoutAgents,
  listScoutRuns,
  resetScoutAgentPairing,
} from "./api";
import type { ScoutAgent, ScoutAgentCreated, ScoutRun } from "./types";

const time = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "Never";

const DEFAULT_PROFILE_DIR =
  "D:\\Bot_Tool_Auto_Game\\scan_pinterest\\pinterest-profile";

export const MIN_SCOUT_CLIENT_VERSION = 38;

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
  const [created, setCreated] = useState<ScoutAgentCreated | null>(null);
  const [name, setName] = useState("Pinterest Auto Scout");
  const [profileDir, setProfileDir] = useState(DEFAULT_PROFILE_DIR);
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState<"agent-id" | "token" | "bootstrap" | "agent" | "">("");

  const onlineAgents = agents.filter(agent => agent.status !== "offline");
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
    const [agentRows, runRows] = await Promise.all([
      listScoutAgents(signal),
      listScoutRuns(undefined, signal),
    ]);
    setAgents(agentRows);
    setRuns(runRows);
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
        <small>PINTEREST SOURCE</small>
        <h2>Auto Scout</h2>
        <p>Persistent Pinterest browser for campaign discovery and reference collection.</p>
      </div>
      <span className={"rrugc-auto-scout-health " + health.tone} title={health.detail}>
        <i aria-hidden="true" />
        {health.label}
      </span>
    </div>

    <div className="rrugc-scout-overview" role="status">
      <span><small>Machines</small><b>{agents.length || "—"} paired</b></span>
      <span><small>Connection</small><b>{onlineAgents.length}/{agents.length || 0} online</b></span>
      <span><small>Task</small><b>{activeRun ? "Scanning" : "Idle"}</b></span>
      <span><small>New refs</small><b>{createdCount}</b></span>
    </div>

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
