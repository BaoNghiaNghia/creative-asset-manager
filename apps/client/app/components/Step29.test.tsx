import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SafeJsonTree } from "./SafeJsonTree";
import { SearchCategoryFilter, SearchGuide, SearchControls } from "./SearchControls";

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
    const markup = renderToStaticMarkup(<SearchControls
      capabilities={{ selected_version: "v3", readiness: "ready", search_available: true, viewer_scoped: false, failure_code: null, facet_names: ["subject"], examples: ["cat OR dog"] }}
      facets={{ subject: [{ value: "cat", count: 3 }] }}
      selected={{ subject: ["cat"] }}
      parsed={{ mode: "or", clauses: [{ kind: "term", value: "cat" }] }}
      onToggle={() => undefined}
    />);
    const guide = renderToStaticMarkup(<SearchGuide capabilities={{ selected_version: "v3", readiness: "ready", search_available: true, viewer_scoped: false, failure_code: null, facet_names: ["subject"], examples: ["cat OR dog"] }} />);
    expect(guide).toContain("H\u01b0\u1edbng d\u1eabn t\u00ecm ki\u1ebfm");
    expect(markup).toContain("subject");
    expect(markup).toContain("Parsed query debug");
  });

  it("renders the fixed design taxonomy as a left-side radio filter", () => {
    const markup = renderToStaticMarkup(<SearchCategoryFilter
      selected={{}}
      onChange={() => undefined}
    />);
    expect(markup).toContain("Design type");
    expect(markup).toContain("PetFull / PeopleFull / CarFull");
    expect(markup).not.toContain("ExistedDesign");
    expect(markup).not.toContain("Other tags");
    expect(markup).toContain('checked=""');
    expect(markup).not.toContain("Image results");
  });
});
