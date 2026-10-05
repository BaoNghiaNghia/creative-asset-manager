import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CenteredLoadingState } from "./CenteredLoadingState";

describe("CenteredLoadingState", () => {
  it("renders a permission-aware centered loading status", () => {
    const markup = renderToStaticMarkup(<CenteredLoadingState
      kind="permissions"
      title="Checking permissions…"
      detail="Verifying workspace access."
    />);
    expect(markup).toContain("centered-loading-state--viewport");
    expect(markup).toContain("centered-loading-state--permissions");
    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("Checking permissions");
    expect(markup).toContain("centered-loading-state__spinner");
  });

  it("uses distinct application icon geometry and supports panel loading", () => {
    const markup = renderToStaticMarkup(<CenteredLoadingState
      kind="application"
      layout="panel"
      title="Loading application…"
    />);
    expect(markup).toContain("centered-loading-state--panel");
    expect(markup).toContain("centered-loading-state--application");
    expect(markup).toContain("<rect");
  });

  it("renders shared errors with the same centered state system without a spinner", () => {
    const markup = renderToStaticMarkup(<CenteredLoadingState
      kind="shared"
      mode="error"
      title="Unable to open shared review"
      detail="This review is unavailable."
    />);
    expect(markup).toContain("centered-loading-state--shared");
    expect(markup).toContain("centered-loading-state--error");
    expect(markup).toContain('role="alert"');
    expect(markup).toContain('aria-busy="false"');
    expect(markup).not.toContain("centered-loading-state__spinner");
    expect(markup).toContain("Unable to open shared review");
  });
});
