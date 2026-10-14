import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { validateSessionCaptureOptions } from "./production-ui-capture.mjs";

const root = path.resolve("/workspace/creative-asset-manager");
test("QA session capture refuses any secret file within or aliasing the Git checkout", () => {
  assert.throws(() => validateSessionCaptureOptions(
    "https://creative-assets.ddns.net",
    "/workspace/creative-asset-manager/session.json", root), /outside the Git/);
  assert.throws(() => validateSessionCaptureOptions(
    "https://creative-assets.ddns.net",
    "/workspace/creative-asset-manager/../creative-asset-manager/nested/session.json", root), /outside the Git/);
});
test("QA session capture only accepts HTTPS without URL credentials", () => {
  for (const value of [
    "http://creative-assets.ddns.net",
    "https://user:pass@creative-assets.ddns.net",
    "https://localhost",
    "https://creative-asset.ddns.net",
    "https://creative-assets.ddns.net:8443",
    "invalid",
  ]) {
    assert.throws(() => validateSessionCaptureOptions(value, "/etc/cam/qa.json", root));
  }
  assert.deepEqual(validateSessionCaptureOptions(
    "https://creative-assets.ddns.net",
    "/home/operator/.config/cam/qa.json", root),
    {origin:"https://creative-assets.ddns.net",output:"/home/operator/.config/cam/qa.json"});
});
