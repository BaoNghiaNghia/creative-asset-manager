import { useCallback, useEffect, useRef, useState } from "react";
import { getRrugcMediaStorageStatus, type RrugcMediaStorageStatus } from "./api";

/** A single reader-scoped status check for Stage 1 / Stage 4, not one probe
 * per image. Never causes regeneration or deletion of the stored outputs.
 */
export function MediaStorageNotice({ active }: { active: boolean }) {
  const [status, setStatus] = useState<RrugcMediaStorageStatus | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const previousDisconnected = useRef(false);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const next = await getRrugcMediaStorageStatus(signal);
      if (signal?.aborted) return;
      setStatus(next);
      setError(false);
      if (previousDisconnected.current && next.connected && !next.reconnect_required) {
        document.dispatchEvent(new Event("rrugc-media-storage-restored"));
      }
      previousDisconnected.current = !next.connected || next.reconnect_required;
    } catch {
      if (!signal?.aborted) setError(true);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    const interval = window.setInterval(() => void refresh(controller.signal), 60_000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [active, refresh]);

  if (!active || (!error && (!status || (status.connected && !status.reconnect_required)))) return null;
  return <div className="rrugc-media-storage-alert" role="alert">
    <span className="rrugc-media-storage-alert-icon" aria-hidden="true">!</span>
    <div className="rrugc-media-storage-alert-copy">
      <strong>{status?.reconnect_required ? "Google Drive Managed Storage cần kết nối lại" : "Không kiểm tra được kết nối ảnh đầu ra"}</strong>
      <span>{status?.reconnect_required
        ? "Ảnh đã tạo vẫn được giữ nguyên. API chưa thể đọc file gốc nên ảnh Stage 1 / Stage 4 có thể hiển thị lỗi 503. Quản trị viên cần kiểm tra tài khoản Drive lưu ảnh."
        : "Ảnh chưa tải được. Kiểm tra kết nối storage rồi thử lại; không cần regenerate hoặc xóa output."}</span>
    </div>
    <div className="rrugc-media-storage-alert-actions">
      <a href="/ai-operations?tab=providers">Kiểm tra Managed Storage ↗</a>
      <button type="button" disabled={loading} onClick={() => void refresh()}>
        {loading ? "Đang kiểm tra…" : "Kiểm tra lại"}
      </button>
    </div>
  </div>;
}
