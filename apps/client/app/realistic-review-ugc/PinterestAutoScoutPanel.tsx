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
  const [copied, setCopied] = useState<"bootstrap" | "agent" | "">("");

  const scout = agents[0] || null;
  const isOnline = Boolean(scout && scout.status !== "offline");

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
          profileDir.trim() || "./.rrugc-pinterest-profile",
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
    if (busy || !name.trim()) return;
    setBusy("pair");
    setCopied("");
    onError("");
    try {
      const next = await createScoutAgent(name.trim());
      setCreated(next);
      await refresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to pair Pinterest Auto Scout.");
    } finally {
      setBusy("");
    }
  }

  async function copy(value: string, kind: "bootstrap" | "agent") {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setCopied(kind);
  }

  const activeRun = runs.find(row => row.status === "claimed" || row.status === "running");
  const lastRun = runs[0] || null;

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
      <span className={"rrugc-auto-scout-health " + (isOnline ? "is-online" : "is-offline")}>
        <i aria-hidden="true" />
        {isOnline ? "Scout online" : "Scout offline"}
      </span>
    </div>

    <div className="rrugc-auto-scout-status rrugc-auto-scout-status-primary">
      <span><small>Browser</small><b>{scout ? "Paired" : "Not paired"}</b></span>
      <span><small>Connection</small><b>{scout?.status?.replaceAll("_", " ") || "Offline"}</b></span>
      <span><small>Task</small><b>{activeRun ? "Scanning" : "Idle"}</b></span>
      <span><small>Last</small><b>{lastRun?.status?.replaceAll("_", " ") || "—"}</b></span>
    </div>

    <details className="rrugc-compact-disclosure" open={created ? true : undefined}>
      <summary>
        <span>
          <strong>Scout setup & diagnostics</strong>
          <small>Single-browser pairing, local-session safety, and last run</small>
        </span>
        <b>{scout ? "1 Scout" : "Setup"}</b>
      </summary>

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
          {busy === "pair"
            ? "Pairing…"
            : scout
              ? "Reset Scout pairing"
              : "Pair local Scout"}
        </button>
      </div>

      <small className="rrugc-scout-safety-note">
        Only one Scout browser is allowed per workspace. Pairing again rotates the token
        for this same Scout instead of creating another agent. Pinterest login/challenges
        stay manual in the local Chrome profile.
      </small>

      {created && <div className="rrugc-command rrugc-auto-scout-command rrugc-token-compact">
        <div className="rrugc-token-compact-head">
          <div>
            <strong>One-time Scout token</strong>
            <small>Restart the local Scout with this command after resetting the pairing.</small>
          </div>
          <span>Token refreshed</span>
        </div>
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
          <button type="button" className="rrugc-primary" onClick={() => void copy(command, "agent")}>
            {copied === "agent" ? "Copied Auto Scout" : "Copy Auto Scout command"}
          </button>
        </div>
        <details className="rrugc-command-preview">
          <summary>View commands</summary>
          <div><small>Bootstrap login</small><code>{bootstrapCommand}</code></div>
          <div><small>Auto Scout</small><code>{command}</code></div>
        </details>
      </div>}

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
