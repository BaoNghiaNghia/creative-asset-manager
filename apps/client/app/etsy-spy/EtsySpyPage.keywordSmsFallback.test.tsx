import {describe, expect, it} from "vitest";
import {titleKeywordFallback} from "./EtsySpyPage";

describe("Keyword SMS title fallback", () => {
  it("returns the literal first comma-delimited clause", () => {
    expect(titleKeywordFallback("Grand Teton National Park Sweatshirt, Vintage Wyoming Crewneck, Moose Mountain"))
      .toBe("Grand Teton National Park Sweatshirt");
  });
  it("works with alternative separators and empty values", () => {
    expect(titleKeywordFallback("Sea Turtle Shirt | Beach Gift")).toBe("Sea Turtle Shirt");
    expect(titleKeywordFallback("Silly Gecko Hoodie; Gift")).toBe("Silly Gecko Hoodie");
    expect(titleKeywordFallback(null)).toBe("");
  });
});
