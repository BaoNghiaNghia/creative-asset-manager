import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PublicTreeSkeleton } from "./PublicSourceTree";

describe("PublicTreeSkeleton", () => {
  it("uses the same skeleton structure as Explorer tree loading", () => {
    const markup = renderToStaticMarkup(<PublicTreeSkeleton count={3} />);
    expect(markup).toContain('class="public-tree-skeleton"');
    expect((markup.match(/public-tree-skeleton-row/g) || []).length).toBe(3);
  });
});
