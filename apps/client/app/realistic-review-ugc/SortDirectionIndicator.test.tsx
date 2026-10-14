import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SortDirectionIndicator } from "./SortDirectionIndicator";
import { StageSortControls } from "./StageSortControls";
import { KeywordImageStage } from "./KeywordImageStage";

describe("Stage 0–2 sort direction visuals", () => {
  it("displays borderless up/down arrows and a neutral idle arrow", () => {
    const asc = renderToStaticMarkup(<SortDirectionIndicator active direction="asc" />);
    const desc = renderToStaticMarkup(<SortDirectionIndicator active direction="desc" />);
    const idle = renderToStaticMarkup(<SortDirectionIndicator active={false} direction="asc" />);
    expect(asc).toContain('data-sort-state="asc"');
    expect(asc).toContain('class="rrugc-sort-indicator is-active is-asc"');
    expect(asc).toContain("↑");
    expect(asc).not.toContain(">ASC<");
    expect(desc).toContain('data-sort-state="desc"');
    expect(desc).toContain("↓");
    expect(desc).not.toContain(">DESC<");
    expect(idle).toContain('data-sort-state="none"');
    expect(idle).not.toContain(">ASC<");
    expect(idle).not.toContain(">DESC<");
  });

  it("marks active Stage 1 Finished at DESC while other columns stay neutral", () => {
    const html = renderToStaticMarkup(<KeywordImageStage active={false}
      skillCatalogRevision={0} onManageSkills={() => undefined} />);
    expect(html).toContain("rrugc-keyword-gen-sort active rrugc-sort-desc");
    expect(html).toContain('data-sort-column="finished_at" data-sort-direction="desc"');
    expect(html).toContain('data-sort-state="desc"');
    expect(html).toContain('data-sort-state="none"');
    expect(html).toContain('aria-sort="descending"');
  });

  it("colors Stage 2 dropdown and direction control from the actual order", () => {
    const desc = renderToStaticMarkup(<StageSortControls stage="Stage 2 designs"
      sortBy="updated" direction="desc" options={["source", "updated", "status"]}
      onChange={() => undefined} />);
    const asc = renderToStaticMarkup(<StageSortControls stage="Stage 2 designs"
      sortBy="source" direction="asc" options={["source", "updated", "status"]}
      onChange={() => undefined} />);
    expect(desc).toContain('class="rrugc-stage-sort is-desc"');
    expect(desc).toContain('data-sort-state="desc"');
    expect(desc).not.toContain(">DESC<");
    expect(asc).toContain('class="rrugc-stage-sort is-asc"');
    expect(asc).toContain('data-sort-state="asc"');
    expect(asc).not.toContain(">ASC<");
    expect(desc).toContain("Reverse sort order for Stage 2 designs");
  });
});
