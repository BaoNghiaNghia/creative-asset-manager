import { type FormEvent, useEffect, useState } from "react";
import {
  getManagedStorageOAuthStatus,
  saveManagedStorageRefreshToken,
  testManagedStorageRefreshToken,
  type ManagedStorageCredentialCheck,
  type ManagedStorageOAuthStatus,
} from "../../features/ai_operations";

const formattedTime = (value: string | null) =>
  value ? new Date(value).toLocaleString("vi-VN") : "Chưa có";

const credentialSource = (source: ManagedStorageOAuthStatus["source"]) =>
  source === "database" ? "Database đã mã hóa" : "Chưa kết nối";

function ManagedStorageIcon() {
  return <span className="ops-managed-storage-icon" aria-hidden="true">
    <svg viewBox="0 0 24 24">
      <path d="M8.1 3.5h7.8l4 6.9-3 5.2H7.1l-3-5.2 4-6.9Z" />
      <path d="m8.1 3.5 4 6.9m7.8 0H12m4.9 5.2-4-6.9M7.1 15.6l4-6.9" />
    </svg>
  </span>;
}

function ManagedStorageHeading() {
  return <div className="ops-managed-storage-heading">
    <ManagedStorageIcon />
    <div>
      <p className="ops-card-kicker">Platform storage</p>
      <h3 id="managed-storage-title">Google Drive Managed Storage</h3>
      <p>Kho Drive riêng cho file tạm của AI, tách biệt với Drive nguồn của tenant.</p>
    </div>
  </div>;
}

export function ManagedStorageCredentialStatus({ status }: { status: ManagedStorageOAuthStatus }) {
  const ready = status.connected && !status.reconnect_required;
  return <>
    <header className="ops-managed-storage-header">
      <ManagedStorageHeading />
      <div className="ops-managed-storage-actions">
        <span className={`ops-connection ${ready ? "ok" : "off"}`}>
          {ready ? "Đã kết nối" : "Cần kết nối lại"}
        </span>
      </div>
    </header>
    {!ready && <p className="ops-managed-storage-guidance">
      <strong>Cần thao tác:</strong> Nhập và kiểm tra refresh token có quyền quản lý thư mục hoạt động.
    </p>}
    <dl className="ops-managed-storage-facts">
      <div><dt>Tài khoản Google</dt><dd>{status.account_email || "Chưa xác định"}</dd></div>
      <div><dt>Nguồn xác thực</dt><dd>{credentialSource(status.source)}</dd></div>
      <div>
        <dt>Thư mục hoạt động</dt>
        <dd>{status.root_folder_configured ? "Đã cấu hình" : "Chưa cấu hình"}</dd>
        {status.root_folder_configured && <small>Nhận diện bằng Folder ID</small>}
      </div>
      <div><dt>Cập nhật gần nhất</dt><dd>{formattedTime(status.updated_at)}</dd></div>
    </dl>
  </>;
}

export function ManagedStorageRefreshTokenForm({
  token, visible, busy, result, onTokenChange, onToggleVisible, onSubmit,
}: {
  token: string;
  visible: boolean;
  busy: "test" | "save" | null;
  result: ManagedStorageCredentialCheck | null;
  onTokenChange: (value: string) => void;
  onToggleVisible: () => void;
  onSubmit: (save: boolean) => void;
}) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit(true);
  };
  return <form className="ops-managed-storage-token-form" onSubmit={submit}>
    <div className="ops-managed-storage-token-copy">
      <h4>Refresh token</h4>
      <p>Token được kiểm tra với Google và quyền đọc/ghi folder active trước khi lưu mã hóa. Giá trị đã lưu sẽ không hiển thị lại.</p>
    </div>
    <div className="ops-managed-storage-token-controls">
      <label>
        <span>Google OAuth refresh token</span>
        <div className="ops-managed-storage-token-input">
          <input
            aria-label="Google Managed Storage refresh token"
            type={visible ? "text" : "password"}
            value={token}
            autoComplete="new-password"
            spellCheck={false}
            onChange={(event) => onTokenChange(event.target.value)}
            placeholder="Nhập refresh token"
          />
          <button type="button" className="secondary" onClick={onToggleVisible}>
            {visible ? "Ẩn" : "Hiện"}
          </button>
        </div>
      </label>
      <div className="ops-managed-storage-token-actions">
        <button type="button" className="secondary" disabled={!token.trim() || busy !== null} onClick={() => onSubmit(false)}>
          {busy === "test" ? "Đang kiểm tra…" : "Kiểm tra token"}
        </button>
        <button type="submit" disabled={!token.trim() || busy !== null}>
          {busy === "save" ? "Đang kiểm tra & lưu…" : "Kiểm tra & lưu"}
        </button>
      </div>
      {result && <p className="ops-inline-success" role="status">
        Token hợp lệ · quyền folder {result.folder_access === "READ_WRITE" ? "đọc/ghi" : result.folder_access}
        {result.account_email ? ` · ${result.account_email}` : ""}
        {result.saved ? " · đã lưu mã hóa" : ""}
      </p>}
    </div>
  </form>;
}

export function ManagedStorageCredentialSettings() {
  const [status, setStatus] = useState<ManagedStorageOAuthStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState("");
  const [tokenVisible, setTokenVisible] = useState(false);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [checkResult, setCheckResult] = useState<ManagedStorageCredentialCheck | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await getManagedStorageOAuthStatus());
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Không thể tải trạng thái Managed Storage.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  const submitToken = async (save: boolean) => {
    if (!token.trim() || busy) return;
    setBusy(save ? "save" : "test");
    setError("");
    setCheckResult(null);
    try {
      const result = save
        ? await saveManagedStorageRefreshToken(token.trim())
        : await testManagedStorageRefreshToken(token.trim());
      setCheckResult(result);
      if (save) {
        setToken("");
        setTokenVisible(false);
        await load();
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Không thể kiểm tra refresh token.");
    } finally {
      setBusy(null);
    }
  };

  return <section className="ops-managed-storage-card" aria-labelledby="managed-storage-title">
    {(!status || loading) && <header className="ops-managed-storage-header"><ManagedStorageHeading /></header>}
    {loading && <p className="ops-managed-storage-loading" aria-busy="true">Đang tải thông tin kết nối…</p>}
    {!loading && error && <p className="ops-inline-error" role="alert">
      {error} <button type="button" onClick={() => void load()}>Thử lại</button>
    </p>}
    {!loading && status && <ManagedStorageCredentialStatus status={status} />}
    {!loading && status?.root_folder_configured && <ManagedStorageRefreshTokenForm
      token={token}
      visible={tokenVisible}
      busy={busy}
      result={checkResult}
      onTokenChange={(value) => { setToken(value); setCheckResult(null); }}
      onToggleVisible={() => setTokenVisible((value) => !value)}
      onSubmit={(save) => void submitToken(save)}
    />}
  </section>;
}
