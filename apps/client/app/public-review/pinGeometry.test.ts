import { describe, expect, it } from "vitest";
import { normalizedPoint, pinPosition, renderedImageRect } from "./pinGeometry";
import { safeHref } from "./RichAnnotation";
describe("public review pin geometry", () => {
 const square = { left: 0, top: 0, width: 200, height: 200 };
 it("uses full square content for square images", () => expect(normalizedPoint(square, 100, 100, 100, 100)).toEqual({ x: .5, y: .5 }));
 it("handles horizontal and vertical letterboxing", () => { expect(renderedImageRect(square, 200, 100)).toEqual({ left: 0, top: 50, width: 200, height: 100 }); expect(renderedImageRect(square, 100, 200)).toEqual({ left: 50, top: 0, width: 100, height: 200 }); });
 it("rejects clicks outside actual image content", () => expect(normalizedPoint(square, 200, 100, 100, 20)).toBeNull());
 it("maps content edges and round trips display position", () => { expect(normalizedPoint(square, 200, 100, 0, 50)).toEqual({ x: 0, y: 0 }); expect(normalizedPoint(square, 200, 100, 200, 150)).toEqual({ x: 1, y: 1 }); expect(pinPosition(square, 200, 100, .25, .75)).toEqual({ x: 50, y: 125 }); });
 it("fails closed for invalid dimensions and normalized values", () => { expect(renderedImageRect(square, 0, 100)).toBeNull(); expect(pinPosition(square, 100, 100, 2, .5)).toBeNull(); });
});
describe("safe public rich links", () => { it("accepts only http and https", () => { expect(safeHref("https://example.com")).toBe("https://example.com/"); expect(safeHref("http://example.com")).toBe("http://example.com/"); expect(safeHref("javascript:alert(1)")).toBeNull(); expect(safeHref("data:text/html,x")).toBeNull(); expect(safeHref("file:///tmp/x")).toBeNull(); }); });

describe("defensive rich annotation rendering", () => {
 it("rejects unknown nodes and never accepts unsafe hrefs", async () => {
  const { renderNode } = await import("./RichAnnotation");
  expect(renderNode({ type: "html", text: "<script>alert(1)</script>" })).toBeNull();
  expect(String(renderNode({ type: "text", text: "x", marks: [{ type: "link", attrs: { href: "javascript:alert(1)" } }] }))).not.toContain("javascript:");
 });
});
