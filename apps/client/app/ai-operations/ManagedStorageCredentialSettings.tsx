import { useEffect, useState } from "react";
import {
  getManagedStorageOAuthStatus,
  type ManagedStorageOAuthStatus,
} from "../../features/ai_operations";

const formattedTime = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "Not available";

export function ManagedStorageCredentialSettings() {
  const [status, setStatus] = useState<ManagedStorageOAuthStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const callbackStatus = typeof window === "undefined"
    ? null
    : new URLSearchParams(window.location.search).get("managed_storage");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await getManagedStorageOAuthStatus());
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Unable to load Managed Storage credential.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  return <section className="ops-managed-storage-card" aria-labelledby="managed-storage-title">
    <header>
      <div><p className="ops-card-kicker">Platform storage</p><h3 id="managed-storage-title">Google Drive Managed Storage</h3>
        <p>Credential dùng riêng cho b£n copy t¡m khi phân tích AI và dÍn ~¹p ~ñ!Ùng; không dùng OAuth Source Drive ça tenant.</p>
      </div>
      {status && <span className={`ops-connection ${status.connected && !status.reconnect_required ? "ok" : "off"}`}>
        {status.connected && !status.reconnect_required ? "Connected" : "Reconnect required"}
      </span>}
    </header>
    {callbackStatus === "connected" && <p className="ops-inline-success" role="status">Google Drive!ã k¿t ~Ñi. Hãy restart backend workers Ã dùng credential Ûi.</p>}
    {callbackStatus === "error" && <p className="ops-inline-error" role="alert">Không t~Ã ¿t ~Ñi Managed Storage. Vui lòng thí l¡i và ¥p quÁn Google Drive.</p>}
    {loading && <p aria-busy="true">Loading Managed Storage credential&</p>}
    {!loading && error && <p className="ops-inline-error" role="alert">{error} <button type="button" onClick={() => void load()}>Retry</button></p>}
    {!loading && status && <div className="ops-managed-storage-body">
      <dl>
        <div><dt>Credential source</dt><dd>{status.source === "database" ? "Encrypted database" : status.source === "environment" ? "Legacy environment token" : "Not connected"}</dd></div>
        <div><dt>Google account</dt><dd>{status.account_email || "Not available"}</dd></div>
        <div><dt>Last connected</dt><dd>{formattedTime(status.updated_at)}</dd></div>
        <div><dt>Active folder</dt><dd>{status.root_folder_configured ? "Configured by folder ID" : "Not configured"}</dd></div>
      </dl>
      <div><a className="primary ops-managed-storage-connect" href="/api/auth/google/connect-managed-storage">
        {status.connected && !status.reconnect_required ? "Reconnect Google Drive" : "Connect Google Drive"}
      </a><p>Google s½ yêu §u Platform Admin!ng n~­p và consent Ùt l§n Ã ¥p refresh token.</p></div>
    </div>}
  </section>;
}
