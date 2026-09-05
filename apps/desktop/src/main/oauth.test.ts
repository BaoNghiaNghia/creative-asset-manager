import { describe, expect, it } from "vitest";
import { createDesktopInstanceNonce, instanceBinding, isDesktopOAuthProvider, isExpectedLaunchUrl } from "./oauth";

describe("desktop OAuth helpers", () => {
  it("keeps a high-entropy nonce out of the renderer contract", () => {
    const nonce = createDesktopInstanceNonce();
    expect(nonce).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(instanceBinding(nonce)).toMatch(/^[a-f0-9]{64}$/);
  });

  it("accepts only supported providers", () => {
    expect(isDesktopOAuthProvider("google")).toBe(true);
    expect(isDesktopOAuthProvider("microsoft")).toBe(true);
    expect(isDesktopOAuthProvider("onedrive")).toBe(false);
  });

  it("requires an exact CAM-origin launch URL", () => {
    const cam = new URL("https://cam.example.com");
    const token = "a".repeat(43);
    expect(isExpectedLaunchUrl("https://cam.example.com/api/v1/desktop/oauth/launch/" + token, cam)).toBe(true);
    expect(isExpectedLaunchUrl("https://cam.example.com.attacker.test/api/v1/desktop/oauth/launch/" + token, cam)).toBe(false);
  });
});
