import { describe, expect, it } from "vitest";
import { buildPipelineMasonryColumns } from "./CreativePipelineTab";

describe("buildPipelineMasonryColumns", () => {
  it("places the next group in the shorter column", () => {
    const groups = [
      { id: "amazon-a" },
      { id: "amazon-b" },
      { id: "etsy-long" },
      { id: "etsy-short" },
    ];
    const rowCounts = new Map([
      ["amazon-a", 0],
      ["amazon-b", 0],
      ["etsy-long", 9],
      ["etsy-short", 2],
    ]);

    const columns = buildPipelineMasonryColumns(groups, rowCounts);

    expect(columns.map((column) => column.map(({ item }) => item.id))).toEqual([
      ["amazon-a", "etsy-long"],
      ["amazon-b", "etsy-short"],
    ]);
    expect(
      columns
        .flat()
        .sort((left, right) => left.order - right.order)
        .map(({ item }) => item.id),
    ).toEqual(groups.map((group) => group.id));
  });

  it("keeps the two columns balanced for mixed card heights", () => {
    const groups = Array.from({ length: 6 }, (_, index) => ({
      id: `group-${index}`,
    }));
    const rowCounts = new Map([
      ["group-0", 6],
      ["group-1", 1],
      ["group-2", 2],
      ["group-3", 5],
      ["group-4", 0],
      ["group-5", 1],
    ]);

    const columns = buildPipelineMasonryColumns(groups, rowCounts);
    const ids = columns.flat().map(({ item }) => item.id);

    expect(new Set(ids).size).toBe(groups.length);
    expect(columns[0].length).toBeGreaterThan(0);
    expect(columns[1].length).toBeGreaterThan(0);
  });
});
