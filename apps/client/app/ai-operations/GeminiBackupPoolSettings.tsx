import { useEffect, useState } from "react";
import { deleteGeminiBackupCredential, listGeminiBackupCredentials, replaceGeminiBackupCredential, testGeminiBackupCredential, type GeminiBackupCredential } from "../../features/ai_operations";

export function GeminiBackupPoolSettings({ canManage = true, embedded = false }: { canManage?: boolean; embedded?: boolean }) {
  const [items, setItems] = useState<GeminiBackupCredential[]>([]);
  const [drafts, setDrafts] = useState<Record<number, { apiKey: string; label: string }>>({});
  const [error, setError] = useState("");
  const load = async () => { try { setItems(await listGeminiBackupCredentials()); } catch (e) { setError(e instanceof Error ? e.message : "Unable to load backup keys"); } };
  useEffect(() => { void load(); }, []);
  const update = (slot: number, field: "apiKey" | "label", value: string) => setDrafts(d => ({ ...d, [slot]: { apiKey: d[slot]?.apiKey || "", label: d[slot]?.label || "", [field]: value } }));
  const add = () => { const available = items.find(item => !item.configured && !drafts[item.slot]); if (available) setDrafts(d => ({ ...d, [available.slot]: { apiKey: "", label: "" } })); };
  const discard = (slot: number) => setDrafts(d => { const next = { ...d }; delete next[slot]; return next; });
  const save = async (slot: number) => { const d = drafts[slot]; if (!d?.apiKey) return; try { const probe = await testGeminiBackupCredential(slot, d.apiKey, d.label || undefined); if (probe.status !== "VALID") { setError(`Backup ${slot}: ${probe.status}`); return; } await replaceGeminiBackupCredential(slot, d.apiKey, d.label || undefined); setDrafts(x => ({ ...x, [slot]: { apiKey: "", label: "" } })); await load(); } catch (e) { setError(e instanceof Error ? e.message : "Unable to save backup key"); } };
  const remove = async (slot: number) => { if (!window.confirm(`Delete backup key ${slot}?`)) return; await deleteGeminiBackupCredential(slot); await load(); };
  return <section className={"inventory-settings-card" + (embedded ? " inventory-settings-card-embedded" : "")}>
    <div className="inventory-settings-heading"><div><p className="inventory-kicker">FAILOVER AI</p><h2>Gemini Backup pool</h2><p className="inventory-muted">Round-robin across active Image backup keys. Configure up to 10 keys.</p></div></div>{canManage && items.some(item => !item.configured && !drafts[item.slot]) && <div className="inventory-actions"><button type="button" onClick={add}>+ Thêm key backup</button></div>}
    {items.filter(item => item.configured || drafts[item.slot]).map(item => <div key={item.slot} className="inventory-credential-grid" style={{ marginTop: 10 }}>
      <div><dt>Backup {item.slot}</dt><dd>{item.masked_key || "Not configured"}</dd></div><div><dt>Label</dt><dd>{item.label || "Not set"}</dd></div><div><dt>Status</dt><dd>{item.configured ? item.status : "Not configured"}</dd></div>
      {canManage && <div><input aria-label={`Backup ${item.slot} API key`} type="password" placeholder="New API key" value={drafts[item.slot]?.apiKey || ""} onChange={e => update(item.slot, "apiKey", e.target.value)} /><input aria-label={`Backup ${item.slot} label`} placeholder="Label" value={drafts[item.slot]?.label || ""} onChange={e => update(item.slot, "label", e.target.value)} /><button type="button" disabled={!drafts[item.slot]?.apiKey} onClick={() => void save(item.slot)}>{item.configured ? "Replace" : "Add key"}</button>{!item.configured && <button type="button" className="secondary" onClick={() => discard(item.slot)}>Cancel</button>}{item.configured && <button type="button" className="danger" onClick={() => void remove(item.slot)}>Delete</button>}</div>}
    </div>)}
    {error && <p className="inventory-error" role="alert">{error}</p>}
  </section>;
}
