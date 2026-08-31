import { useEffect, useState } from "react";
import {
  inventoryApi,
  InventoryApiError,
  type InventoryAiCredential,
  type InventoryGeminiCredentialStatus,
} from "./api";

type State = "loading" | "ready" | "forbidden" | "error";
type Draft = { apiKey: string; label: string };

const emptyDraft = (): Draft => ({ apiKey: "", label: "" });

export function credentialStatusLabel(status: string, configured: boolean): string {
  if (!configured) return "Not configured";
  return ({
    connected: "Connected",
    VALID: "Connected",
    INVALID_KEY: "Invalid key",
    PERMISSION_DENIED: "Permission denied",
    RATE_LIMITED: "Rate limited",
    PROVIDER_UNAVAILABLE: "Unavailable",
    unavailable: "Unavailable",
  } as Record<string, string>)[status] || status;
}

export function clearCredentialDraft(): Draft {
  return emptyDraft();
}

export function credentialStatusClass(status: string, configured: boolean): string {
  if (!configured) return "not-configured";
  return ({
    connected: "connected",
    VALID: "connected",
    INVALID_KEY: "invalid",
    PERMISSION_DENIED: "denied",
    RATE_LIMITED: "limited",
    PROVIDER_UNAVAILABLE: "unavailable",
    unavailable: "unavailable",
  } as Record<string, string>)[status] || "unavailable";
}

function formatTimestamp(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "Not available";
}

function ErrorMessage({ error }: { error: string }) {
  return <p className="inventory-error" role="alert">{error}</p>;
}

export function InventoryGeminiCredentialSettings({
  initialCredential,
  canManage = true,
  embedded = false,
}: {
  initialCredential?: InventoryAiCredential;
  canManage?: boolean;
  embedded?: boolean;
}) {
  const cardClass = "inventory-settings-card" + (embedded ? " inventory-settings-card-embedded" : "");
  const [credential, setCredential] = useState<InventoryAiCredential | null>(initialCredential || null);
  const [state, setState] = useState<State>(initialCredential ? "ready" : "loading");
  const [message, setMessage] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [showKey, setShowKey] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [testingCurrent, setTestingCurrent] = useState(false);
  const [currentTestButtonStatus, setCurrentTestButtonStatus] = useState<string | null>(null);
  const [testStatus, setTestStatus] = useState<InventoryGeminiCredentialStatus | null>(null);

  const load = async () => {
    setState("loading");
    setMessage("");
    try {
      setCredential(await inventoryApi.getAiCredential());
      setState("ready");
    } catch (error) {
      setState(error instanceof InventoryApiError && error.status === 403 ? "forbidden" : "error");
      setMessage(error instanceof Error ? error.message : "Unable to load Gemini credential");
    }
  };

  useEffect(() => {
    if (!initialCredential) void load();
  }, [initialCredential]);

  const closeDialog = () => {
    setDialogOpen(false);
    setDraft(clearCredentialDraft());
    setShowKey(false);
    setTestStatus(null);
    setMessage("");
  };

  const testConnection = async (): Promise<InventoryGeminiCredentialStatus | null> => {
    if (!draft.apiKey || submitting) return null;
    setSubmitting(true);
    setMessage("");
    try {
      const result = await inventoryApi.testAiCredential(draft.apiKey, draft.label || undefined);
      setTestStatus(result.status);
      return result.status;
    } catch (error) {
      if (error instanceof InventoryApiError && error.status === 403) setState("forbidden");
      setMessage(error instanceof Error ? error.message : "Unable to test Gemini credential");
      return null;
    } finally {
      setSubmitting(false);
    }
  };

  const testCurrentConnection = async () => {
    if (!credential?.configured || submitting || testingCurrent) return;
    setTestingCurrent(true);
    setCurrentTestButtonStatus("Testing…");
    setMessage("");
    try {
      const result = await inventoryApi.testAiCredential();
      setTestStatus(result.status);
      setCurrentTestButtonStatus(credentialStatusLabel(result.status, true));
    } catch (error) {
      if (error instanceof InventoryApiError && error.status === 403) setState("forbidden");
      setMessage(error instanceof Error ? error.message : "Unable to test Gemini credential");
      setCurrentTestButtonStatus("Test failed");
    } finally {
      setTestingCurrent(false);
      window.setTimeout(() => setCurrentTestButtonStatus(null), 5000);
    }
  };

  const save = async () => {
    if (!draft.apiKey || submitting) return;
    setSubmitting(true);
    setMessage("");
    try {
      const test = await inventoryApi.testAiCredential(draft.apiKey, draft.label || undefined);
      setTestStatus(test.status);
      if (test.status !== "VALID") return;
      const updated = await inventoryApi.replaceAiCredential(draft.apiKey, draft.label || undefined);
      setCredential(updated);
      setState("ready");
      closeDialog();
    } catch (error) {
      if (error instanceof InventoryApiError && error.status === 403) setState("forbidden");
      setMessage(error instanceof Error ? error.message : "Unable to save Gemini credential");
    } finally {
      setSubmitting(false);
    }
  };

  if (state === "loading") {
    return <section className={cardClass} aria-busy="true"><h2>Inventory AI</h2><p>Loading Gemini credential configuration…</p></section>;
  }
  if (state === "forbidden") {
    return <section className={cardClass}><h2>Inventory AI</h2><ErrorMessage error="You do not have permission to view Inventory Gemini credential configuration." /></section>;
  }
  if (state === "error" || !credential) {
    return <section className={cardClass}><h2>Inventory AI</h2><ErrorMessage error={message || "Unable to load Gemini credential configuration."} /><button type="button" onClick={() => void load()}>Retry</button></section>;
  }

  return <section className={cardClass} aria-labelledby="inventory-ai-title">
    <div className="inventory-settings-heading">
      <div>
        <p className="inventory-kicker">Inventory AI</p>
        <h2 id="inventory-ai-title">Gemini cho Inventory</h2>
        <p className="inventory-muted">Dùng riêng cho pipeline tài liệu Inventory; không ảnh hưởng đến Creative AI.</p>
      </div>
      <span className={"inventory-credential-status status-" + credentialStatusClass(credential.status, credential.configured)}>{credentialStatusLabel(credential.status, credential.configured)}</span>
    </div>
    <dl className="inventory-credential-grid">
      <div><dt>Provider</dt><dd>Google Gemini</dd></div>
      <div><dt>Status</dt><dd>{credentialStatusLabel(credential.status, credential.configured)}</dd></div>
      <div><dt>API Key</dt><dd>{credential.masked_key || "Not configured"}</dd></div>

      <div><dt>Label</dt><dd>{credential.label || "Not set"}</dd></div>

      <div><dt>Last Updated</dt><dd>{formatTimestamp(credential.updated_at)}</dd></div>

    </dl>
    <div className="inventory-actions">
      {canManage ? <>
        <button type="button" className="secondary" onClick={() => void testCurrentConnection()} disabled={!credential.configured || submitting || currentTestButtonStatus !== null}><span className={currentTestButtonStatus ? "inventory-test-button-text" : undefined}>{currentTestButtonStatus || "Test Connection"}</span></button>
        <button type="button" onClick={() => { setDialogOpen(true); setMessage(""); }}>Replace API Key</button>
      </> : <p className="inventory-muted">You can view this credential status, but <code>inventory.credentials.manage</code> is required to test or replace the key.</p>}
    </div>
    {message ? <ErrorMessage error={message} /> : null}
    {dialogOpen ? <div className="inventory-modal-backdrop" role="presentation" onMouseDown={closeDialog}>
      <section className="inventory-modal" role="dialog" aria-modal="true" aria-labelledby="replace-gemini-title" onMouseDown={event => event.stopPropagation()}>
        <h2 id="replace-gemini-title">Replace Gemini API Key</h2>
        <p className="inventory-muted">The existing key is not shown. The label is user-defined and does not identify a Google account.</p>
        <label>API Key
          <div className="inventory-key-input">
            <input aria-label="Gemini API key" type={showKey ? "text" : "password"} value={draft.apiKey} autoComplete="off" onChange={event => setDraft(value => ({ ...value, apiKey: event.target.value }))} />
            <button type="button" onClick={() => setShowKey(value => !value)}>{showKey ? "Hide key" : "Show key"}</button>
          </div>
        </label>
        <label>Label (optional)
          <input aria-label="Gemini credential label" placeholder="Gemini Account B" value={draft.label} onChange={event => setDraft(value => ({ ...value, label: event.target.value }))} />
        </label>
        {testStatus ? <p className="inventory-test-result" role="status">{credentialStatusLabel(testStatus, true)}</p> : null}
        {message ? <ErrorMessage error={message} /> : null}
        <div className="inventory-actions">
          <button type="button" onClick={() => void testConnection()} disabled={!draft.apiKey || submitting}>{submitting ? "Testing…" : "Test Connection"}</button>
          <button type="button" onClick={() => void save()} disabled={!draft.apiKey || submitting}>{submitting ? "Saving…" : "Test & Save"}</button>
          <button type="button" className="secondary" onClick={closeDialog} disabled={submitting}>Cancel</button>
        </div>
      </section>
    </div> : null}
  </section>;
}
