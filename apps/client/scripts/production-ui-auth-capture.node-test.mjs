import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { validateCaptureTarget } from "./production-ui-auth-capture.mjs";

const safeOutput = path.join(os.tmpdir(), "cam-qa-session-test.json");

test("session capture requires HTTPS without URL credentials", () => {
  assert.throws(
    () => validateCaptureTarget("http://creative-assets.ddns.net", safeOutput),
    /HTTPS URL/,
  );
  assert.throws(
    () => validateCaptureTarget("https://user:pass@creative-assets.ddns.net", safeOutput),
    /HTTPS URL/,
  );
});

test("session capture refuses credential files inside source checkout", () => {
  const insideRepo = fileURLToPath(new URL("../qa-storage.json", import.meta.url));
  assert.throws(
    () => validateCaptureTarget("https://creative-assets.ddns.net", insideRepo),
    /outside the repository/,
  );
  assert.throws(
    () => validateCaptureTarget("https://creative-assets.ddns.net", ""),
    /--output/,
  );
});

test("session capture permits an external protected target", () => {
  const validated = validateCaptureTarget(
    "https://creative-assets.ddns.net",
    safeOutput,
  );
  assert.equal(validated.output, path.resolve(safeOutput));
  assert.equal(validated.url.protocol, "https:");
});
