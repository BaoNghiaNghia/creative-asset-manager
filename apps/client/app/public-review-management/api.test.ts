import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ManagementApiError,
  managementApi,
  resolveShareLinkForCopy,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("public review management API", () => {
  it("preserves structured API error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "current_share_link_unavailable",
              message: "Current share link is unavailable",
            },
          }),
          {
            status: 409,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    await expect(managementApi.current("share-a")).rejects.toMatchObject({
      status: 409,
      code: "current_share_link_unavailable",
    } satisfies Partial<ManagementApiError>);
  });

  it("rotates once when the legacy current link cannot be recovered", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: {
              code: "current_share_link_unavailable",
              message: "Current share link is unavailable",
            },
          }),
          {
            status: 409,
            headers: { "Content-Type": "application/json" },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "share-a",
            public_id: "public-a",
            name: "Review",
            status: "active",
            allow_comments: true,
            allow_download: false,
            expires_at: "2026-10-01T00:00:00Z",
            created_at: "2026-09-24T00:00:00Z",
            updated_at: "2026-09-24T00:00:00Z",
            revoked_at: null,
            scopes: [],
            share_url: "https://assets.example.test/share/public-a#key=new-secret",
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolveShareLinkForCopy("share-a");

    expect(result).toEqual({
      share_url: "https://assets.example.test/share/public-a#key=new-secret",
      expires_at: "2026-10-01T00:00:00Z",
      rotated: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toContain("/share-a/current-link");
    expect(fetchMock.mock.calls[1][0]).toContain("/share-a/rotate-secret");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
  });

  it("does not rotate when the current link is recoverable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          share_url: "https://assets.example.test/share/public-a#key=current-secret",
          expires_at: null,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await resolveShareLinkForCopy("share-a");

    expect(result.rotated).toBe(false);
    expect(result.share_url).toContain("current-secret");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
