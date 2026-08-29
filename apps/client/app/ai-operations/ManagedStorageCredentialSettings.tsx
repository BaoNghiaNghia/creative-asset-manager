import { useEffect, useState } from "react";
import {
  getManagedStorageOAuthStatus,
  type ManagedStorageOAuthStatus,
} from "../../features/ai_operations";

const formattedTime = (value: string | null) =>
  value ? new Date(value).toLocaleString("vi-VN") : "Chưa có";

const credentialSource = (source: ManagedStorageOAuthStatus["source"]) => {
  if (source === "database") return "Database đã mã hóa";
  if (source === "environment") return "Biến môi trường (legacy)";
  return "Chưa kết nối";
};

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
        <a className="primary ops-managed-storage-connect" href="/api/auth/google/connect-managed-storage">
          {ready ? "Kết nối lại" : "Kết nối Google Drive"}
        </a>
      </div>
    </header>

    {!ready && <p className="ops-managed-storage-guidance">
      <strong>Cần thao tác:</strong> Kết nối Google Drive để cấp refresh token cho tác vụ lưu trữ nền.
    </p>}

    <dl className="ops-managed-storage-facts">
      <div>
        <dt>Tài khoản Google</dt>
        <dd>{status.account_email || "Chưa xác định"}</dd>
      </div>
      <div>
        <dt>Nguồn xác thực</dt>
        <dd>{credentialSource(status.source)}</dd>
      </div>
      <div>
        <dt>Thư mục hoạt động</dt>
        <dd>{status.root_folder_configured ? "Đã cấu hình" : "Chưa cấu hình"}</dd>
        {status.root_folder_configured && <small>Nhận diện bằng Folder ID</small>}
      </div>
      <div>
        <dt>Cập nhật gần nhất</dt>
        <dd>{formattedTime(status.updated_at)}</dd>
      </div>
    </dl>
  </>;
}

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
      setError(failure instanceof Error ? failure.message : "Không thể tải trạng thái Managed Storage.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  return <section className="ops-managed-storage-card" aria-labelledby="managed-storage-title">
    {(!status || loading) && <header className="ops-managed-storage-header">
      <ManagedStorageHeading />
    </header>}
    {callbackStatus === "connected" && <p className="ops-inline-success" role="status">
      Google Drive đã kết nối thành công.
    </p>}
    {callbackStatus === "error" && <p className="ops-inline-error" role="alert">
      Không thể kết nối Managed Storage. Vui lòng thử lại và chấp thuận quyền Google Drive.
    </p>}
    {loading && <p className="ops-managed-storage-loading" aria-busy="true">
      Đang tải thông tin kết nối…
    </p>}
    {!loading && error && <p className="ops-inline-error" role="alert">
      {error} <button type="button" onClick={() => void load()}>Thử lại</button>
    </p>}
    {!loading && status && <ManagedStorageCredentialStatus status={status} />}
  </section>;
}
