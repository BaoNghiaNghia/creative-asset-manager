import { describe, expect, it } from "vitest";
import { oauthMessageFor, uploadErrorMessage } from "./useDriveExplorer";

describe("oauthMessageFor", () => {
  it("explains local bootstrap admission failures", () => {
    expect(oauthMessageFor("self_signup_disabled")).toContain("administrator");
    expect(oauthMessageFor("tenant_membership_required")).toContain("workspace membership");
    expect(oauthMessageFor("account_inactive")).toContain("suspended or disabled");
  });

  it("keeps a safe fallback for unknown callback codes", () => {
    expect(oauthMessageFor("unexpected_internal_code")).toBe(
      "Cloud sign-in could not be completed.",
    );
  });
});


describe("uploadErrorMessage", () => {
  it("keeps the safe API upload failure detail", () => {
    expect(uploadErrorMessage({ detail: "Google Drive write access is required." }))
      .toBe("Google Drive write access is required.");
  });

  it("uses a safe fallback for malformed upload failures", () => {
    expect(uploadErrorMessage(null)).toBe("Upload failed. Try again.");
  });
});
