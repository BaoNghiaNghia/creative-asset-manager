// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PublicReviewManagementDialog } from "./PublicReviewManagementDialog";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function response(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("PublicReviewManagementDialog", () => {
  it("renders the share manager as a body-level modal portal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ items: [] })));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<PublicReviewManagementDialog
        initialScope={{
          external_source_id: "source-1",
          folder_external_id: "folder-1",
          folder_name: "First Grade",
        }}
        availableScopes={[{
          external_source_id: "source-1",
          folder_external_id: "folder-1",
          folder_name: "First Grade",
        }]}
        onClose={() => undefined}
      />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const modal = document.body.querySelector(".public-review-management-backdrop");
    expect(modal).not.toBeNull();
    expect(host.querySelector(".public-review-management-backdrop")).toBeNull();
    expect(modal?.textContent).toContain("Share for review");
    expect(modal?.textContent).toContain("Create review link");
    expect(modal?.textContent).toContain("Existing shares");
    expect(modal?.textContent).toContain("First Grade");

    await act(async () => root.unmount());
  });
});
