import type {
  VideoCdnDeliveryObservability,
  VideoCdnDeliveryRuntimeStatus,
} from "./types";

type Fetcher = typeof fetch;

export class SettingsApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(
  url: string,
  init: RequestInit,
  fetcher: Fetcher,
): Promise<T> {
  const response = await fetcher(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    credentials: "same-origin",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as {
      detail?: string | { message?: string };
    };
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : payload.detail?.message;
    throw new SettingsApiError(
      detail || `Request failed (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export function fetchVideoCdnDeliveryRuntimeStatus(
  fetcher: Fetcher = fetch,
): Promise<VideoCdnDeliveryRuntimeStatus> {
  return request(
    "/api/v1/admin/video-delivery/runtime",
    { method: "GET" },
    fetcher,
  );
}

export function updateVideoCdnDeliveryRuntime(
  enabled: boolean,
  reason: string,
  fetcher: Fetcher = fetch,
): Promise<VideoCdnDeliveryRuntimeStatus> {
  return request(
    "/api/v1/admin/video-delivery/runtime",
    {
      method: "PUT",
      body: JSON.stringify({ enabled, reason }),
    },
    fetcher,
  );
}


export function fetchVideoCdnDeliveryObservability(
  fetcher: Fetcher = fetch,
): Promise<VideoCdnDeliveryObservability> {
  return request(
    "/api/v1/admin/video-delivery/observability",
    { method: "GET" },
    fetcher,
  );
}
