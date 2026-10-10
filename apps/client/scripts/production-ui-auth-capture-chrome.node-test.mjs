import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { appOnlyStorageState, validateQaProfile } from "./production-ui-auth-capture-chrome.mjs";

test("only Creative Asset Management cookies and local storage may be exported", () => {
  const state = {
    cookies: [
      { name: "qa", domain: "creative-assets.ddns.net", value: "app-token" },
      { name: "qa-sub", domain: ".creative-assets.ddns.net", value: "app-token-2" },
      { name: "google", domain: ".google.com", value: "NEVER-EXPORT" },
      { name: "gaccounts", domain: "accounts.google.com", value: "NEVER-EXPORT" },
      { name: "other", domain: "untrustedcreative-assets.ddns.net", value: "NEVER-EXPORT" },
      { name: "parent", domain: ".ddns.net", value: "NEVER-EXPORT" },
    ],
    origins: [
      { origin: "https://creative-assets.ddns.net", localStorage: [{ name: "ui", value: "ok" }] },
      { origin: "https://accounts.google.com", localStorage: [{ name: "gauth", value: "NEVER-EXPORT" }] },
    ],
  };
  const filtered = appOnlyStorageState(state, "https://creative-assets.ddns.net");
  assert.deepEqual(filtered.cookies.map(cookie => cookie.name), ["qa", "qa-sub"]);
  assert.deepEqual(filtered.origins, [state.origins[0]]);
  assert.ok(!JSON.stringify(filtered).includes("NEVER-EXPORT"));
});

test("a dedicated profile must be specified outside the repository", () => {
  assert.throws(() => validateQaProfile(""), /--profile-dir/);
  assert.throws(() => validateQaProfile(path.resolve("apps/client")), /outside the repository/);
  assert.equal(validateQaProfile(path.join(os.tmpdir(), "cam-qa-isolated")), path.join(os.tmpdir(), "cam-qa-isolated"));
});

test("missing storage cookies will not be silently synthesized", () => {
  assert.deepEqual(appOnlyStorageState({}, "https://creative-assets.ddns.net"), { cookies: [], origins: [] });
});
