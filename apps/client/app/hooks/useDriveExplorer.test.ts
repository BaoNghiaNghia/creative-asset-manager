import { describe, expect, it } from "vitest";
import { oauthMessageFor } from "./useDriveExplorer";

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
