// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MediaStorageNotice } from "./MediaStorageNotice";
import { getRrugcMediaStorageStatus } from "./api";

vi.mock("./api", () => ({
  getRrugcMediaStorageStatus: vi.fn(),
}));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); document.body.replaceChildren(); });

describe("Stage 1 / Stage 4 managed-storage diagnostics", () => {
  it("shows an actionable warning without deleting existing output records", async () => {
    vi.mocked(getRrugcMediaStorageStatus).mockResolvedValue({
      status: "reconnect_required", root_folder_configured: true,
      connected: false, reconnect_required: true, retry_after_seconds: 60,
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<MediaStorageNotice active />);
      await Promise.resolve();
    });
    expect(host.textContent).toContain("Google Drive Managed Storage cần kết nối lại");
    expect(host.textContent).toContain("Ảnh đã tạo vẫn được giữ nguyên");
    expect(host.querySelector('a[href="/ai-operations?tab=providers"]')).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("triggers a shared image retry after recovery instead of regenerating artwork", async () => {
    vi.useFakeTimers();
    const mocked = vi.mocked(getRrugcMediaStorageStatus);
    mocked.mockResolvedValueOnce({
      status: "reconnect_required", root_folder_configured: true,
      connected: false, reconnect_required: true, retry_after_seconds: 60,
    }).mockResolvedValueOnce({
      status: "connected", root_folder_configured: true,
      connected: true, reconnect_required: false, retry_after_seconds: 60,
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const recovered = vi.fn();
    document.addEventListener("rrugc-media-storage-restored", recovered);
    await act(async () => {
      root.render(<MediaStorageNotice active />);
      await Promise.resolve();
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(recovered).toHaveBeenCalledOnce();
    expect(host.querySelector(".rrugc-media-storage-alert")).toBeNull();
    await act(async () => root.unmount());
    document.removeEventListener("rrugc-media-storage-restored", recovered);
  });
});
