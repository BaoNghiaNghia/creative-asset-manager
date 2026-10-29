import { describe, expect, it } from "vitest";
import { formatRelativeDateTime } from "./RelativeDateTime";

describe("Stage 0 Created date and Stage 1 Picked date", () => {
  const now = Date.parse("2026-10-29T08:00:00Z");
  const ago = (ms: number) => new Date(now - ms).toISOString();
  it("shares Finished at 24-hour formatting", () => {
    expect(formatRelativeDateTime(ago(30_000), now)).toBe("Vừa xong");
    expect(formatRelativeDateTime(ago(61_000), now)).toBe("1 phút trước");
    expect(formatRelativeDateTime(ago(2 * 3600_000), now)).toBe("2h trước");
    expect(formatRelativeDateTime(ago(23 * 3600_000), now)).toBe("23h trước");
    expect(formatRelativeDateTime(ago(24 * 3600_000), now)).toContain("2026");
    expect(formatRelativeDateTime(ago(25 * 3600_000), now)).toContain("2026");
    expect(formatRelativeDateTime(null, now)).toBe("—");
    expect(formatRelativeDateTime("invalid", now)).toBe("—");
  });
});
