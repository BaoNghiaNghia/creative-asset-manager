import { useEffect, useMemo, useState } from "react";
import { createScoutAgent, listScoutAgents } from "../realistic-review-ugc/api";
import type { ScoutAgent, ScoutAgentCreated } from "../realistic-review-ugc/types";

const safeError = (error: unknown) => error instanceof Error ? error.message : "Scout pairing request failed";

/** The one-time token is deliberately NEVER persisted in localStorage or logs. */
export function TiktokShopAgentPanel() {
  const [agents, setAgents] = useState<ScoutAgent[]>([]);
  const [created, setCreated] = useState<ScoutAgentCreated | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  async function refresh(signal?: AbortSignal) {
    const rows = await listScoutAgents(signal, "tiktokshop");
    if (!signal?.aborted) setAgents(rows);
  }
  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal).catch(reason => {
      if (!controller.signal.aborted) setError(safeError(reason));
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh().catch(() => undefined);
    }, 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);

  async function pairNew() {
    if (working) return;
    setError("");
    setWorking(true);
    try {
      const row = await createScoutAgent("TikTokShop Windows Scout", "tiktokshop");
      setCreated(row);
      await refresh();
    } catch (reason) {
      setError(safeError(reason));
    } finally {
      setWorking(false);
    }
  }

  const needsPinterestLogin = agents.some(agent =>
    agent.active && (agent.status === "needs_login" ||
      /pinterest_.*login_required/.test(agent.last_error_code || "")));
  const hasLinkedAgent = agents.some(agent => agent.active && agent.last_seen_at);

  const config = useMemo(() => created ? [
    "RRUGC_TIKTOKSHOP_AGENT_ID=" + created.id,
    "RRUGC_TIKTOKSHOP_SCOUT_TOKEN=" + created.agent_token,
  ].join("\n") : "", [created]);

  return <section className="rrugc-card" aria-label="TikTokShop Windows Scout pairing"
    style={{ padding: 16, marginBottom: 12 }}>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
      <div>
        <strong>Windows · TikTokShop Scout</strong>
        <p style={{ margin: "4px 0 0" }}>Separate Agent ID, token, Chrome profile and campaign queue. GatorHats Scout is unaffected.</p>
      </div>
      <button type="button" className="rrugc-primary rrugc-icon-action"
        onClick={() => void pairNew()} disabled={working}>
        {working ? "Pairing…" : hasLinkedAgent ? "Pair another agent" : "Pair TikTokShop agent"}
      </button>
    </div>
    {agents.length > 0 && <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
      {agents.map(agent => <span key={agent.id} className="rrugc-source-auto-badge"
        title={agent.name + " · " + (agent.machine_label || "no machine")}>
        {agent.name} · {agent.status}
      </span>)}
    </div>}
    {needsPinterestLogin && <p role="status" style={{ marginTop: 10 }}>
      <strong>Pinterest login required.</strong> The TikTokShop agent is paired but cannot
      scout until its dedicated Chrome profile is signed in. On DESKTOP-3FR0LF0,
      open <code>START_SCOUT_TIKTOKSHOP.cmd</code>, finish Pinterest sign-in in
      the Chrome window it opens, and close that Chrome window. The agent will
      verify the saved session automatically; do not create a new pairing.
    </p>}
    {agents.some(agent => agent.active && agent.status === "offline" && !agent.last_error_code)
      && !needsPinterestLogin && <p style={{ marginTop: 10 }}>
        An agent is offline. Start TikTokShop Scout on the Windows tool machine;
        pairing a second agent does not start the existing one.
      </p>}
    {error && <p role="alert" style={{ marginTop: 8 }}>{error}</p>}
    {created && <div style={{ marginTop: 12 }}>
      <strong>One-time pairing credentials — copy to Windows Scout Manager</strong>
      <p>Save these once. The token will not be shown again after you leave this page.</p>
      <textarea readOnly value={config} rows={2}
        aria-label="TikTokShop Scout pairing settings"
        style={{ width: "100%", minHeight: 82, resize: "vertical", fontFamily: "monospace", fontSize: 12 }}/>
      <button type="button" className="rrugc-icon-action" onClick={() => {
        void navigator.clipboard.writeText(config).then(() => setCopied(true)).catch(() => setError("Clipboard unavailable. Select and copy the text manually."));
      }}>{copied ? "Copied" : "Copy pairing"}</button>
      <button type="button" className="rrugc-icon-action" style={{ marginLeft: 8 }}
        onClick={() => setCreated(null)}>Hide secret</button>
    </div>}
  </section>;
}
