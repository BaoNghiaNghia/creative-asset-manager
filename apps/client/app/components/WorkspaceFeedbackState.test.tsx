// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { WorkspaceFeedbackState, WorkspaceSkeletonRows } from "./WorkspaceFeedbackState";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("shared evondevKit operation states", () => {
  it("provides a polite accessible loading state without a misleading retry", () => {
    const html = renderToStaticMarkup(<WorkspaceFeedbackState kind="loading" title="Loading jobs" detail="Reading queue" />);
    expect(html).toContain('role="status"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("cam-workspace-feedback-spinner");
    expect(html).not.toContain("cam-workspace-feedback-action");
  });

  it("distinguishes empty data from technical errors and exposes a working retry", async () => {
    const empty = renderToStaticMarkup(<WorkspaceFeedbackState kind="empty" title="No matching jobs" />);
    expect(empty).toContain('role="status"');
    expect(empty).not.toContain('role="alert"');
    const onRetry = vi.fn();
    const rootNode = document.createElement("div");
    document.body.appendChild(rootNode);
    const root = createRoot(rootNode);
    try {
      await act(async () => root.render(<WorkspaceFeedbackState kind="error" title="Request failed" detail="Network unavailable" onRetry={onRetry} />));
      expect(rootNode.querySelector('[role="alert"]')).not.toBeNull();
      expect(rootNode.textContent).toContain("Network unavailable");
      await act(async () => rootNode.querySelector<HTMLButtonElement>(".cam-workspace-feedback-action")!.click());
      expect(onRetry).toHaveBeenCalledTimes(1);
    } finally {
      await act(async () => root.unmount());
      rootNode.remove();
    }
  });

  it("renders bounded, non-interactive skeleton rows with an accessible label", () => {
    const html = renderToStaticMarkup(<WorkspaceSkeletonRows rows={28} />);
    expect(html.match(/class="cam-workspace-skeleton-row"/g)).toHaveLength(12);
    expect(html).toContain('aria-label="Loading records"');
    expect(html).toContain('aria-hidden="true"');
  });
});
