import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RrugcActionIcon } from "./RrugcActionIcon";
import type { RrugcActionIconName } from "./RrugcActionIcon";

describe("RRUGC action icons", () => {
  it("renders a consistent accessible decorative SVG for each supported action", () => {
    const actions: RrugcActionIconName[] = [
      "scan", "refresh", "skills", "sparkles", "play", "stop",
      "select-all", "deselect", "chevron-left", "chevron-right",
      "chevrons-left", "chevrons-right", "folder", "sort", "filter", "check", "logs",
    ];
    for (const name of actions) {
      const markup = renderToStaticMarkup(<RrugcActionIcon name={name} />);
      expect(markup).toContain(`data-ui-icon="${name}"`);
      expect(markup).toContain('aria-hidden="true"');
      expect(markup).toContain('viewBox="0 0 24 24"');
      expect(markup).toMatch(/<(path|rect|circle)\b/);
    }
  });
});
