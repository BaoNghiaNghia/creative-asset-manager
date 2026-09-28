import { useEffect, useMemo, useState } from "react";
import {
  archiveScoutAgent,
  createScoutAgent,
  listScoutAgents,
  listScoutRuns,
} from "./api";
import type { ScoutAgent, ScoutAgentCreated, ScoutRun } from "./types";

const time = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "Never";

export function autoScoutCommand(
  baseUrl: string,
  agentId: string,
  token: string,
): string {
  const url = baseUrl.replace(/\/$/, "");
  return [
    "python apps/rrugc_scout/scout.py",
    "--base-url \"" + url + "\"",
    "--agent-id \"" + agentId + "\"",
    "--token \"" + token + "\"",
    "--profile-dir \"./.rrugc-pinterest-profile\"",
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
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState(false);

  const command = useMemo(
    () => created
      ? autoScoutCommand(window.location.origin, created.id, created.agent_token)
      : "",
    [created],
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
    setBusy("create");
    setCopied(false);
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

  async function archive(agentId: string) {
    if (busy) return;
    setBusy(agentId);
    onError("");
    try {
      await archiveScoutAgent(agentId);
      if (created?.id === agentId) setCreated(null);
      await refresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to archive Scout Agent.");
    } finally {
      setBusy("");
    }
  }

  async function copy() {
    if (!command) return;
    await navigator.clipboard.writeText(command);
    setCopied(true);
  }

  const online = agents.filter(row => row.status !== "offline").length;
  const activeRun = runs.find(row => row.status === "claimed" || row.status === "running");
  const lastRun = runs[0] || null;

  return <section className="rrugc-card rrugc-auto-scout-panel" aria-label="Pinterest Auto Scout">
    <div className="rrugc-section-heading rrugc-auto-scout-heading">
      <div>
        <small>PINTEREST SOURCE</small>
        <h2>Auto Scout</h2>
        <p>
          Pair a browser machine once. Auto Scout then picks up due campaigns,
          collects visible Pinterest references, and hands them to the existing QA
          and Drive pipeline automatically.
        </p>
      </div>
      <span className={"rrugc-auto-scout-health " + (online ? "is-online" : "is-offline")}>
        <i aria-hidden="true" />
        {online ? online + " online" : "Agent offline"}
      </span>
    </div>

    <div className="rrugc-auto-scout-status rrugc-auto-scout-status-primary">
      <span><small>Paired</small><b>{agents.length}</b></span>
      <span><small>Online</small><b>{online}</b></span>
      <span><small>Task</small><b>{activeRun ? "Scanning" : "Idle"}</b></span>
      <span><small>Last</small><b>{lastRun?.status?.replaceAll("_", " ") || "—"}</b></span>
    </div>

    <details className="rrugc-compact-disclosure" open={created ? true : undefined}>
      <summary>
        <span><strong>Scout setup & diagnostics</strong><small>Pairing, local-session safety, agent list, and last run</small></span>
        <b>{agents.length ? agents.length + " paired" : "Setup"}</b>
      </summary>
      <div className="rrugc-auto-scout-grid">
        <div className="rrugc-auto-scout-pair">
          <div className="rrugc-auto-scout-pair-copy">
            <strong>Connect a browser machine</strong>
            <small>One pairing per machine. Pinterest session data never leaves that browser.</small>
          </div>
          <label>
            Agent name
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
            {busy === "create" ? "Pairing…" : "Pair local Scout"}
          </button>
        </div>
      </div>

      <div className="rrugc-auto-scout-safety">
        <strong>Local session boundary</strong>
        <span>
          Login and Pinterest challenges stay manual. Auto Scout pauses safely,
          keeps the browser open, and resumes after normal access returns.
        </span>
      </div>

    {created && <div className="rrugc-command rrugc-auto-scout-command">
      <div>
        <strong>One-time Agent token</strong>
        <p>
          Shown only now. Run this on the machine that has Chrome/Chromium and your
          Pinterest session. Keep the process running; it will claim campaigns automatically.
        </p>
      </div>
      <code>{command}</code>
      <button type="button" onClick={() => void copy()}>
        {copied ? "Copied" : "Copy Auto Scout command"}
      </button>
    </div>}

    {agents.length > 0 && <div className="rrugc-auto-scout-agents">
      {agents.map(agent =>
        <article key={agent.id}>
          <span className={"rrugc-agent status-" + agent.status}>{agent.status}</span>
          <div>
            <strong>{agent.name}</strong>
            <small>
              {agent.machine_label || "Machine not connected yet"}
              {" · last seen "}{time(agent.last_seen_at)}
            </small>
            {agent.last_error_code && <small className="rrugc-auto-scout-error">
              {agent.last_error_code.replaceAll("_", " ")}
            </small>}
          </div>
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={() => void archive(agent.id)}
          >
            {busy === agent.id ? "Archiving…" : "Archive"}
          </button>
        </article>
      )}
    </div>}

    {lastRun && <div className="rrugc-auto-scout-last-run">
      <span><small>Latest run</small><b>{lastRun.status.replaceAll("_", " ")}</b></span>
      <span><small>Submitted</small><b>{lastRun.submitted_count}</b></span>
      <span><small>New Pins</small><b>{lastRun.created_count}</b></span>
      <span><small>Existing</small><b>{lastRun.existing_count}</b></span>
      <span><small>Started</small><b>{time(lastRun.started_at)}</b></span>
    </div>}
    </details>
  </section>;
}
