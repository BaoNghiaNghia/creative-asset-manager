import { describe, expect, it } from "vitest";
import {
  classifyNavigation,
  isInternalCamUrl,
  resolveDesktopUrl,
} from "./navigation";

describe("resolveDesktopUrl", () => {
  it("allows localhost HTTP only in development", () => {
    expect(resolveDesktopUrl(undefined, false).toString()).toBe(
      "http://localhost:5173/",
    );
    expect(
      resolveDesktopUrl("http://127.0.0.1:5173", false).toString(),
    ).toBe("http://127.0.0.1:5173/");
  });

  it("rejects remote HTTP in production", () => {
    expect(() =>
      resolveDesktopUrl("http://cam.example.com", true),
    ).toThrow("HTTPS");
  });

  it("rejects non-web schemes", () => {
    expect(() => resolveDesktopUrl("file:///tmp/cam", false)).toThrow(
      "HTTPS",
    );
    expect(() => resolveDesktopUrl("javascript:alert(1)", false)).toThrow(
      "HTTPS",
    );
  });
});

describe("classifyNavigation", () => {
  const camUrl = new URL("https://cam.example.com");

  it("allows the CAM origin and its subpaths internally", () => {
    expect(classifyNavigation("https://cam.example.com", camUrl)).toBe(
      "internal",
    );
    expect(
      classifyNavigation("https://cam.example.com/folder/123", camUrl),
    ).toBe("internal");
    expect(
      isInternalCamUrl("https://cam.example.com/folder/123", camUrl),
    ).toBe(true);
  });

  it("classifies external HTTPS separately", () => {
    expect(classifyNavigation("https://docs.example.com/help", camUrl)).toBe(
      "external",
    );
  });

  it.each(["javascript:alert(1)", "file:///tmp/cam", "data:text/plain,cam"])(
    "rejects dangerous URL %s",
    (target) => {
      expect(classifyNavigation(target, camUrl)).toBe("rejected");
    },
  );

  it("does not accept a lookalike CAM host", () => {
    expect(
      classifyNavigation("https://cam.example.com.attacker.test", camUrl),
    ).toBe("external");
  });
});
