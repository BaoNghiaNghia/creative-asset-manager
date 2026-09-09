import { useEffect, useState } from "react";
import {
  deleteGeminiBackupCredential,
  listGeminiBackupCredentials,
  replaceGeminiBackupCredential,
  testGeminiBackupCredential,
  type GeminiBackupCredential,
} from "../../features/ai_operations";

type Draft = { apiKey: string; label: string };

const formatTime = (value: string | null) => value ? new Date(value).toLocaleString() : "Not available";

export function GeminiBackupPoolSettings({ canManage = true, embedded = false }: { canManage?: boolean; embedded?: boolean }) {
const TestIcon = () => <svg className="gemini-backup-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 5v4h4M4 13a8.1 8.1 0 0 0 15.5 2M20 19v-4h-4" /></svg>;
const ReplaceIcon = () => <svg className="gemini-backup-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m14 4 6 6M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z" /></svg>;
const DeleteIcon = () => <svg className="gemini-backup-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" /></svg>;

  const [items, setItems] = useState<GeminiBackupCredential[]>([]);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testing, setTesting] = useState<number | null>(null);

  const load = async () => {
    try {
      setError("");
      setItems(await listGeminiBackupCredentials());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load backup keys");
    }
  };
  useEffect(() => { void load(); }, []);

  const startDraft = (slot: number) => setDrafts(current => ({
    ...current,
    [slot]: current[slot] || { apiKey: "", label: "" },
  }));
  const discard = (slot: number) => setDrafts(current => {
    const next = { ...current };
    delete next[slot];
    return next;
  });
  const update = (slot: number, field: keyof Draft, value: string) => setDrafts(current => ({
    ...current,
    [slot]: { ...(current[slot] || { apiKey: "", label: "" }), [field]: value },
  }));
  const add = () => {
    const slot = items.find(item => !item.configured && !drafts[item.slot])?.slot;
    if (slot) startDraft(slot);
  };
  const save = async (slot: number) => {
    const draft = drafts[slot];
    if (!draft?.apiKey) return;
    try {
      setError("");
      const probe = await testGeminiBackupCredential(slot, draft.apiKey, draft.label || undefined);
      if (probe.status !== "VALID") {
        setError(`Backup ${slot}: ${probe.status}`);
        return;
      }
      await replaceGeminiBackupCredential(slot, draft.apiKey, draft.label || undefined);
      discard(slot);
      setNotice(`Backup ${slot} has been saved.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save backup key");
    }
  };
  const testCurrent = async (slot: number) => {
    try {
      setTesting(slot);
      setError("");
      const result = await testGeminiBackupCredential(slot);
      setNotice(`Backup ${slot}: ${result.status}${result.http_status ? ` (HTTP ${result.http_status})` : ""}`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to test backup key");
    } finally {
      setTesting(null);
    }
  };
  const remove = async (slot: number) => {
    if (!window.confirm(`Delete backup key ${slot}? This cannot be undone.`)) return;
    try {
      setError("");
      await deleteGeminiBackupCredential(slot);
      discard(slot);
      setNotice(`Backup ${slot} has been deleted.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to delete backup key");
    }
  };

  const rows = items.filter(item => item.configured || drafts[item.slot]);
  const canAdd = canManage && items.some(item => !item.configured && !drafts[item.slot]);

  return <section className={"gemini-backup-pool" + (embedded ? " gemini-backup-pool-embedded" : " inventory-settings-card")} aria-label="Gemini Image backup key pool">
    <div className="gemini-backup-pool-heading">
      <div>
        <p className="inventory-kicker">FAILOVER AI</p>
        <h3>Backup key pool</h3>
        <p className="inventory-muted">Active keys rotate automatically. Hover a row to manage it.</p>
      </div>
      <span className="inventory-credential-status status-connected">{items.filter(item => item.configured).length}/10 active</span>
    </div>

    <div className="gemini-backup-pool-table" role="table" aria-label="Image backup keys">
      <div className="gemini-backup-pool-head" role="row">
        <span role="columnheader">Backup key</span>
        <span role="columnheader">Label</span>
        <span role="columnheader">Status and updated</span>
      </div>
      {rows.map(item => <div key={item.slot} className="gemini-backup-pool-entry">
        <div className="gemini-backup-pool-row" role="row" tabIndex={0}>
          <div role="cell"><strong>Backup {item.slot}</strong><span>{item.masked_key || "Not configured"}</span></div>
          <div role="cell"><strong>{item.label || "Not set"}</strong></div>
          <div role="cell">
            <span className={"inventory-credential-status " + (item.configured ? "status-connected" : "status-not-configured")}>{item.configured ? item.status : "Not configured"}</span>
            <small>{formatTime(item.updated_at)}</small>
          </div>
          {canManage && item.configured && <div className="gemini-backup-pool-actions">
            <button type="button" className="secondary" disabled={testing === item.slot} onClick={() => void testCurrent(item.slot)}><TestIcon />{testing === item.slot ? "Testing..." : "Test"}</button>
            <button type="button" onClick={() => startDraft(item.slot)}><ReplaceIcon />Replace</button>
            <button type="button" className="danger" onClick={() => void remove(item.slot)}><DeleteIcon />Delete</button>
          </div>}
        </div>
        {canManage && drafts[item.slot] && <form className="gemini-backup-pool-editor" onSubmit={event => { event.preventDefault(); void save(item.slot); }}>
          <label>New API key<input aria-label={`Backup ${item.slot} API key`} type="password" autoComplete="off" value={drafts[item.slot].apiKey} onChange={event => update(item.slot, "apiKey", event.target.value)} /></label>
          <label>Label<input aria-label={`Backup ${item.slot} label`} value={drafts[item.slot].label} onChange={event => update(item.slot, "label", event.target.value)} /></label>
          <div><button type="submit" disabled={!drafts[item.slot].apiKey}>{item.configured ? "Test and replace" : "Test and save"}</button><button type="button" className="secondary" onClick={() => discard(item.slot)}>Cancel</button></div>
        </form>}
      </div>)}
      {canAdd && <button type="button" className="gemini-backup-pool-add" onClick={add}>+ Add backup key</button>}
    </div>

    {!rows.length && <p className="inventory-muted">No backup key has been configured yet.</p>}
    {notice && <p className="inventory-test-result" role="status">{notice}</p>}
    {error && <p className="inventory-error" role="alert">{error}</p>}
  </section>;
}
