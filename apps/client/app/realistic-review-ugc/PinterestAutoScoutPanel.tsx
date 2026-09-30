import { useEffect, useMemo, useState } from "react";
import {
  createScoutAgent,
  listScoutAgents,
  listScoutRuns,
} from "./api";
import type { ScoutAgent, ScoutAgentCreated, ScoutRun } from "./types";

const time = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "Never";

const DEFAULT_PROFILE_DIR =
  "D:\\Bot_Tool_Auto_Game\\scan_pinterest\\pinterest-profile";

export const MIN_SCOUT_CLIENT_VERSION = 9;

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
    "RRUGC_DETAIL_CONCURRENCY=3",
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
  const [copied, setCopied] = useState<"config" | "bootstrap" | "agent" | "">("");

  const scout = agents[0] || null;
  const isOnline = Boolean(scout && scout.status !== "offline");
  const clientCurrent = Boolean(scout && scoutClientIsCurrent(scout.client_version));

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
  const localConfig = useMemo(
    () => created
      ? scoutLocalConfig(
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
    setAgents(agentRows.slice(0, 1));
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
    const nextName = scout?.name?.trim() || name.trim();
    if (busy || !nextName) return;
    setBusy("pair");
    setCopied("");
    onError("");
    try {
      const next = await createScoutAgent(nextName);
      setCreated(next);
      await refresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to pair Pinterest Auto Scout.");
    } finally {
      setBusy("");
    }
  }

  async function copy(value: string, kind: "config" | "bootstrap" | "agent") {
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
  const health = !isOnline
    ? { tone: "is-offline", label: "Scout offline", detail: "Run START_SCOUT.bat on the Scout machine; it will update and reconnect automatically." }
    : !clientCurrent
      ? {
          tone: "is-warning",
          label: "Scout update recommended",
          detail: "This Scout is " + (scout?.client_version || "an unknown version")
            + ". Update the local client to rrugc-scout-v" + MIN_SCOUT_CLIENT_VERSION
            + "+. Run START_SCOUT.bat to update automatically before the next scan.",
        }
    : isStalled
      ? { tone: "is-warning", label: "Scan stalled", detail: "No run heartbeat for more than 2 minutes. The lease watchdog will release it automatically." }
      : scout?.status === "needs_login"
        ? { tone: "is-warning", label: "Login required", detail: "Open the persistent Pinterest profile and complete login." }
        : scout?.status === "error" || recentFailures >= 3
          ? { tone: "is-warning", label: "Recovery needed", detail: "Recent Scout runs are failing repeatedly; check diagnostics below." }
          : { tone: "is-online", label: activeRun ? "Scanning normally" : "Pipeline healthy", detail: activeRun ? "Heartbeat is current and the campaign lease is active." : "Scout is online and ready for the next campaign." };

  return <section className="rrugc-card rrugc-auto-scout-panel" aria-label="Pinterest Auto Scout">
    <div className="rrugc-section-heading rrugc-auto-scout-heading">
      <div>
        <small>PINTEREST SOURCE</small>
        <h2>Auto Scout</h2>
        <p>
          One workspace uses one persistent Scout browser. It picks up due campaigns,
          collects visible Pinterest references, and hands them to the existing QA
          and Drive pipeline automatically.
        </p>
      </div>
      <span className={"rrugc-auto-scout-health " + health.tone} title={health.detail}>
        <i aria-hidden="true" />
        {health.label}
      </span>
    </div>

    <div className="rrugc-auto-scout-status rrugc-auto-scout-status-primary">
      <span><small>Browser</small><b>{scout ? "Paired" : "Not paired"}</b></span>
      <span><small>Connection</small><b>{scout?.status?.replaceAll("_", " ") || "Offline"}</b></span>
      <span><small>Task</small><b>{activeRun ? "Scanning" : "Idle"}</b></span>
      <span><small>Client</small><b>{scout?.client_version || "—"}</b></span>
    </div>

    <div className="rrugc-pipeline-health" role="status">
      <div><small>PIPELINE HEALTH</small><strong>{health.label}</strong><p>{health.detail}</p></div>
      <div className="rrugc-pipeline-metrics">
        <span><small>20-run yield</small><b>{yieldRate === null ? "—" : yieldRate + "%"}</b></span>
        <span><small>Duplicates</small><b>{duplicateRate === null ? "—" : duplicateRate + "%"}</b></span>
        <span><small>New refs</small><b>{createdCount}</b></span>
        <span><small>Run failures</small><b>{recentFailures}</b></span>
      </div>
    </div>

    <details className="rrugc-compact-disclosure" open={created ? true : undefined}>
      <summary>
        <span>
          <strong>Scout connection</strong>
          <small>One-click launcher, pairing, and diagnostics</small>
        </span>
        <b>{scout ? (isOnline ? "Connected" : "Paired") : "Setup"}</b>
      </summary>

      {!scout ? <>
        <div className="rrugc-scout-setup-row">
          <label>
            <span>Scout name</span>
            <input
              value={name}
              maxLength={160}
              onChange={event => setName(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="rrugc-primary"
            disabled={Boolean(busy) || !name.trim()}
            onClick={() => void pairAgent()}
          >
            {busy === "pair" ? "Pairing…" : "Pair local Scout"}
          </button>
        </div>
        <small className="rrugc-scout-safety-note">
          Pair once, then use START_SCOUT.bat on the Scout machine. The launcher updates
          the client before every start and reuses the persistent Pinterest profile.
        </small>
      </> : <div className="rrugc-scout-connected-row">
        <div>
          <strong>{scout.name}</strong>
          <small>
            Use START_SCOUT.bat for normal starts. Reset pairing only when you need to
            rotate the local Scout token.
          </small>
        </div>
        <button
          type="button"
          className="rrugc-scout-reset"
          disabled={Boolean(busy)}
          onClick={() => void pairAgent()}
        >
          {busy === "pair" ? "Resetting…" : "Reset pairing"}
        </button>
      </div>}

      {created && <div className="rrugc-launcher-setup">
        <div className="rrugc-token-compact-head">
          <div>
            <strong>Pairing refreshed</strong>
            <small>Update the local config once, then START_SCOUT.bat handles future updates automatically.</small>
          </div>
          <span>One-time token</span>
        </div>
        <div className="rrugc-launcher-steps">
          <span><b>1</b><small>On the Scout PC, pull main once if START_SCOUT.bat is not there yet.</small></span>
          <span><b>2</b><small>Copy this pairing into scout.local.env.</small></span>
          <span><b>3</b><small>Run START_SCOUT.bat. No Python command is needed after that.</small></span>
        </div>
        <div className="rrugc-token-actions">
          <button type="button" className="rrugc-primary" onClick={() => void copy(localConfig, "config")}>
            {copied === "config" ? "Copied scout.local.env" : "Copy scout.local.env"}
          </button>
        </div>
        <small className="rrugc-launcher-token-note">
          The token is only shown through this one-time copy action and stays in the Git-ignored local config.
        </small>
      </div>}

      <details className="rrugc-scout-advanced">
        <summary>
          <span>
            <strong>Advanced manual setup</strong>
            <small>Login recovery or launcher troubleshooting only</small>
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
              {copied === "bootstrap" ? "Copied login command" : "Copy login command"}
            </button>
            {created && <button type="button" onClick={() => void copy(command, "agent")}>
              {copied === "agent" ? "Copied manual Scout command" : "Copy manual Scout command"}
            </button>}
          </div>
          <details className="rrugc-command-preview">
            <summary>View manual commands</summary>
            <div><small>Bootstrap login</small><code>{bootstrapCommand}</code></div>
            {created && <div><small>Auto Scout fallback</small><code>{command}</code></div>}
          </details>
        </div>
      </details>

      {scout && <div className="rrugc-last-run-compact">
        <span><small>Scout</small><b>{scout.name}</b></span>
        <span><small>Machine</small><b>{scout.machine_label || "Not connected"}</b></span>
        <span><small>Status</small><b>{scout.status.replaceAll("_", " ")}</b></span>
        <span><small>Last seen</small><b>{time(scout.last_seen_at)}</b></span>
      </div>}

      {scout?.last_error_code && <small className="rrugc-auto-scout-error">
        {scout.last_error_code.replaceAll("_", " ")}
      </small>}

      {lastRun && <div className="rrugc-last-run-compact">
        <span><small>Latest run</small><b>{lastRun.status.replaceAll("_", " ")}</b></span>
        <span><small>Submitted</small><b>{lastRun.submitted_count}</b></span>
        <span><small>New</small><b>{lastRun.created_count}</b></span>
        <span><small>Started</small><b>{time(lastRun.started_at)}</b></span>
      </div>}
    </details>
  </section>;
}
