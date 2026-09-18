import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PublicTreeSkeleton } from "./PublicSourceTree";

describe("PublicTreeSkeleton", () => {
  it("renders the requested number of source-tree loading rows", () => {
    const markup = renderToStaticMarkup(<PublicTreeSkeleton count={3} />);
    expect(markup).toContain('aria-label="Loading folders"');
    expect((markup.match(/public-tree-skeleton-row/g) || []).length).toBe(3);
  });
});
