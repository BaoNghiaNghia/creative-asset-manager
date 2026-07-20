import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SafeJsonTree } from "./SafeJsonTree";
import { SearchV2Controls } from "./SearchV2Controls";

describe("Step 29 operator UI", () => {
  it("renders bounded nested metadata without mutating it", () => {
    const value = { subject: { species: "cat" }, values: [1, 2] };
    const before = JSON.stringify(value);
    const markup = renderToStaticMarkup(<SafeJsonTree value={value} maxDepth={8} maxNodes={30} />);
    expect(markup).toContain("subject");
    expect(markup).toContain("cat");
    expect(JSON.stringify(value)).toBe(before);
  });

  it("shows syntax examples, facets and privileged parsed debug", () => {
    const markup = renderToStaticMarkup(<SearchV2Controls
      capabilities={{ selected_version: "v2", v2_available: true, parser_available: true, debug_allowed: true, facet_names: ["subject"], examples: ["cat OR dog"] }}
      facets={{ subject: [{ value: "cat", count: 3 }] }}
      selected={{ subject: ["cat"] }}
      parsed={{ mode: "or", clauses: [{ kind: "term", value: "cat" }] }}
      onToggle={() => undefined}
    />);
    expect(markup).toContain("Search syntax");
    expect(markup).toContain("subject");
    expect(markup).toContain("Parsed query debug");
  });
});
