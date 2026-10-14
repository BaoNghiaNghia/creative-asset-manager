import { useCallback, useEffect, useState } from "react";
import { getCodexAccounts, updateCodexAccount } from "./api";
import type { CodexAccountConfig, CodexAccountsStatus } from "./api";
import "./CodexAccountManager.css";

const STAGES = ["stage1", "stage2", "stage4"] as const;
const displayStage: Record<string, string> = {
  stage1: "Stage 1 · Designs", stage2: "Stage 2 · Colorways",
  stage4: "Stage 4 · UGC",
};
const statuses: Record<string, string> = {
  connected: "Connected", not_connected: "Not connected",
  auth_required: "Reconnection required", cli_unavailable: "Codex CLI unavailable",
  check_failed: "Connection check failed",
};

export function CodexAccountManager({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<CodexAccountsStatus | null>(null);
  const [draft, setDraft] = useState<Record<string, CodexAccountConfig>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [guide, setGuide] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const response = await getCodexAccounts(signal);
      if (signal?.aborted) return;
      setData(response);
      setDraft(Object.fromEntries(response.accounts.map(account => [account.account_id, account])));
      setError("");
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "Unable to check Codex accounts.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function change(accountId: string, patch: Partial<CodexAccountConfig>) {
    setDraft(current => ({ ...current, [accountId]: { ...current[accountId], ...patch } }));
    setNotice("");
  }

  async function save(accountId: CodexAccountConfig["account_id"]) {
    const settings = draft[accountId];
    if (!settings || busy) return;
    setBusy(accountId); setNotice(""); setError("");
    try {
      const updated = await updateCodexAccount(accountId, {
        display_name: settings.display_name,
        enabled: settings.enabled,
        max_concurrent: settings.max_concurrent,
        stages: settings.stages,
      });
      setData(updated);
      setDraft(Object.fromEntries(updated.accounts.map(row => [row.account_id, row])));
      setNotice("Configuration saved. The active worker still uses Account 01.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save Codex account settings.");
    } finally { setBusy(null); }
  }

  return <div className="rrugc-codex-pool-backdrop" role="presentation"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="rrugc-codex-pool-dialog" role="dialog" aria-modal="true"
      aria-labelledby="rrugc-codex-pool-heading">
      <header className="rrugc-codex-pool-heading">
        <div>
          <div className="rrugc-codex-pool-kicker">REALISTIC REVIEW UGC · SETTINGS</div>
          <h2 id="rrugc-codex-pool-heading">Codex Account Manager</h2>
          <p>Manage separate Codex profiles without exposing credentials in CAM.</p>
        </div>
        <button type="button" className="rrugc-codex-pool-close" aria-label="Close Codex accounts" onClick={onClose}>×</button>
      </header>
      <div className="rrugc-codex-pool-banner" role="status">
        <strong>Routing: Single active account</strong>
        <span>{data?.routing_note || "Account 01 continues to run existing Codex jobs; secondary profile scheduling is not enabled."}</span>
      </div>
      {error && <p className="rrugc-codex-pool-error" role="alert">{error}</p>}
      {notice && <p className="rrugc-codex-pool-notice" role="status">{notice}</p>}
      {loading ? <p className="rrugc-codex-pool-loading" role="status">Checking Codex accounts…</p> : null}
      {data?.accounts.map(account => {
        const current = draft[account.account_id] || account;
        const disabled = busy !== null || !data.can_manage;
        return <article className="rrugc-codex-pool-account" key={account.account_id}>
          <div className="rrugc-codex-pool-account-top">
            <div>
              <strong>{account.account_id === "account-1" ? "Account 01 · Existing" : "Account 02 · Secondary"}</strong>
              <div className="rrugc-codex-pool-subtitle">
                <span className={"rrugc-codex-pool-status is-" + account.auth_status}>
                  {statuses[account.auth_status] || account.auth_status}
                </span>
                {account.is_executing && <span className="rrugc-codex-pool-live">Current worker profile</span>}
              </div>
            </div>
            <label className="rrugc-codex-pool-enabled">
              <input type="checkbox" checked={current.enabled} disabled={disabled}
                onChange={event => change(account.account_id, { enabled: event.target.checked })}/>
              Enabled in pool config
            </label>
          </div>
          <div className="rrugc-codex-pool-fields">
            <label>Display name
              <input type="text" maxLength={80} value={current.display_name} disabled={disabled}
                onChange={event => change(account.account_id, { display_name: event.target.value })}/>
            </label>
            <label>Requested concurrency (future scheduler)
              <select value={current.max_concurrent} disabled={disabled}
                onChange={event => change(account.account_id, { max_concurrent: Number(event.target.value) })}>
                {[1, 2, 3].map(n => <option value={n} key={n}>{n} job{n > 1 ? "s" : ""}</option>)}
              </select>
            </label>
          </div>
          <div className="rrugc-codex-pool-stages" role="group" aria-label={account.account_id + " stages"}>
            {STAGES.map(stage => <label key={stage}>
              <input type="checkbox" checked={current.stages.includes(stage)} disabled={disabled}
                onChange={event => change(account.account_id, {
                  stages: event.target.checked
                    ? [...current.stages, stage]
                    : current.stages.filter(item => item !== stage),
                })}/>
              {displayStage[stage]}
            </label>)}
          </div>
          <div className="rrugc-codex-pool-actions">
            <button type="button" onClick={() => setGuide(guide === account.account_id ? null : account.account_id)}>
              {guide === account.account_id ? "Hide connection guide" : "Connection guide"}
            </button>
            <button type="button" disabled={loading || busy !== null} onClick={() => void load()}>
              Check status
            </button>
            {data.can_manage && <button type="button" className="is-primary"
              disabled={disabled || !current.display_name.trim()} onClick={() => void save(account.account_id)}>
              {busy === account.account_id ? "Saving…" : "Save configuration"}
            </button>}
          </div>
          {guide === account.account_id && <div className="rrugc-codex-pool-guide">
            <p>Complete device sign-in through a trusted VPS terminal as the Codex service user. Never paste passwords or tokens into CAM.</p>
            <code>{account.account_id === "account-1"
              ? "sudo bash scripts/cam-codex-runtime.sh login"
              : "sudo bash scripts/cam-codex-runtime.sh login-2"}</code>
            <p>After signing in, click “Check status”. Enabling the second profile does not automatically switch running jobs or bypass provider quotas.</p>
          </div>}
        </article>;
      })}
      <footer className="rrugc-codex-pool-footer">
        <span>{data?.can_manage ? "Account preferences only · Scheduler integration pending" : "Read-only · Tenant administrator required to edit"}</span>
        <button type="button" onClick={onClose}>Close</button>
      </footer>
    </section>
  </div>;
}
