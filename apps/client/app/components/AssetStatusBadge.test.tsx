import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssetStatusBadge } from "./AssetStatusBadge";
import type { AssetProcessingStatus } from "../types";

const cases: Array<[AssetProcessingStatus, string]> = [
  ["discovered", "Discovered"],
  ["stored", "Stored"],
  ["analyzing", "Analyzing"],
  ["metadata_ready", "Metadata ready"],
  ["indexed", "Indexed"],
  ["duplicate", "Duplicate"],
  ["failed", "Failed"],
];

describe("AssetStatusBadge", () => {
  it.each(cases)("renders the %s state accessibly", (status, label) => {
    const markup = renderToStaticMarkup(<AssetStatusBadge status={status} />);

    expect(markup).toContain('data-status="' + status + '"');
    expect(markup).toContain(label);
    expect(markup).toContain("Processing status:");
  });
});
