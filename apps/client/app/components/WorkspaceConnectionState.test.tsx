// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceConnectionState } from "./WorkspaceConnectionState";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("Workspace connection fallback UI", () => {
  it("matches the existing CAM error shell without loading heavy construction media", () => {
    const html = renderToStaticMarkup(<WorkspaceConnectionState mode="reconnecting" onRetry={() => {}} />);
    expect(html).toContain("cam-under-construction");
    expect(html).toContain("cam-under-brand");
    expect(html).toContain("Creative assets");
    expect(html).toContain("Connection interrupted");
    expect(html).toContain("Retry connection");
    expect(html).toContain("Back to Asset Explorer");
    expect(html).toContain('role="status"');
    expect(html).toContain("The current page URL will be preserved");
    expect(html).not.toContain("<video");
    expect(html).not.toContain("/construction-media/");
  });

  it("uses an explicit permission / session recovery message instead of implying an outage", () => {
    const expired = renderToStaticMarkup(<WorkspaceConnectionState mode="unauthenticated" />);
    expect(expired).toContain("Session expired");
    expect(expired).toContain("Return to sign in");
    expect(expired).not.toContain("Retrying your connection automatically");
    const denied = renderToStaticMarkup(<WorkspaceConnectionState mode="denied" />);
    expect(denied).toContain("Access restricted");
    expect(denied).toContain("Contact an administrator");
    expect(denied).not.toContain("Return to sign in");
  });

  it("retry triggers the existing access gate, not a full page reload", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onRetry = vi.fn();
    await act(async () => root.render(<WorkspaceConnectionState mode="reconnecting" onRetry={onRetry} />));
    await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
    expect(onRetry).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
    host.remove();
  });
});
