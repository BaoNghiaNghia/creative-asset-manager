import assert from "node:assert/strict";
import test from "node:test";
import { isExpectedQaRequestAbort } from "./ui-qa-request-classification.mjs";

test("read-only completion feed cancelled by stage navigation is benign", () => {
  assert.equal(isExpectedQaRequestAbort({
    method: "GET",
    url: "http://127.0.0.1:49000/api/v1/realistic-review-ugc/stage4/completions?cursor=start",
    error: "net::ERR_ABORTED",
  }, "normal", "http://127.0.0.1:49000/realistic-review-ugc"), true);
});

test("read-only source-plan sorting request cancelled by workflow navigation is benign", () => {
  const base = "http://127.0.0.1:49000/api/v1/realistic-review-ugc/source-plans";
  const query = "?page=1&page_size=10&sort_by=source&sort_dir=asc";
  assert.equal(isExpectedQaRequestAbort({method:"GET", url:base + query,
    error:"net::ERR_ABORTED"}, "normal", "http://127.0.0.1:49000/realistic-review-ugc"), true);
  assert.equal(isExpectedQaRequestAbort({method:"GET", url:base,
    error:"net::ERR_ABORTED"}, "normal", "http://127.0.0.1:49000/realistic-review-ugc"), false);
});

test("read-only keyword intelligence GET cancelled by Stage 2 navigation is benign", () => {
  const site = "http://127.0.0.1:49000/realistic-review-ugc";
  const url = "http://127.0.0.1:49000/api/v1/realistic-review-ugc/keyword-analysis/search-intelligence";
  assert.equal(isExpectedQaRequestAbort({ method: "GET", url, error: "net::ERR_ABORTED" }, "normal", site), true);
  for (const req of [
    { method: "POST", url, error: "net::ERR_ABORTED" },
    { method: "GET", url, error: "net::ERR_CONNECTION_REFUSED" },
    { method: "GET", url: url.replace("search-intelligence", "signals"), error: "net::ERR_ABORTED" },
    { method: "GET", url: url.replace("127.0.0.1", "another-host.test"), error: "net::ERR_ABORTED" },
  ]) assert.equal(isExpectedQaRequestAbort(req, "normal", site), false);
});

test("inventory incident QA ignores only fixture identity GET cancellations", () => {
  const site = "http://127.0.0.1:49000/inventory/pipeline";
  const same = "http://127.0.0.1:49000/api/v1/auth/identity";
  const identityAbort = { method: "GET", url: same, error: "net::ERR_ABORTED" };
  assert.equal(isExpectedQaRequestAbort(identityAbort, "inventory-incident", site), true);
  assert.equal(isExpectedQaRequestAbort(identityAbort, "normal", site), false);
  for (const request of [
    {method:"POST",url:same,error:"net::ERR_ABORTED"},
    {method:"GET",url:same,error:"net::ERR_CONNECTION_REFUSED"},
    {method:"GET",url:same.replace("identity","permissions"),error:"net::ERR_ABORTED"},
    {method:"GET",url:"http://other-host.invalid/api/v1/auth/identity",error:"net::ERR_ABORTED"},
  ]) assert.equal(isExpectedQaRequestAbort(request,"inventory-incident",site),false);
});

test("never hide other request failures or unexpected aborted mutations", () => {
  const base = "http://127.0.0.1:49000/api/v1/realistic-review-ugc/stage4/completions";
  for (const request of [
    { method: "GET", url: base, error: "net::ERR_CONNECTION_REFUSED" },
    { method: "POST", url: base, error: "net::ERR_ABORTED" },
    { method: "GET", url: base.replace("completions", "status"), error: "net::ERR_ABORTED" },
    { method: "GET", url: "http://other-host.invalid/api/v1/realistic-review-ugc/stage4/completions", error: "net::ERR_ABORTED" },
  ]) assert.equal(isExpectedQaRequestAbort(request, "normal", "http://127.0.0.1:49000/realistic-review-ugc"), false);
});

test("aborted same-origin font loads on viewport teardown do not fail UI QA", () => {
  const site = "http://127.0.0.1:49000/realistic-review-ugc";
  assert.equal(isExpectedQaRequestAbort({
    method: "GET", url: "http://127.0.0.1:49000/assets/be-vietnam-pro-vietnamese-700-normal.woff2",
    error: "net::ERR_ABORTED",
  }, "normal", site), true);
  for (const failure of [
    { method:"GET", url:"http://other.example/assets/font.woff2", error:"net::ERR_ABORTED" },
    { method:"GET", url:"http://127.0.0.1:49000/assets/font.woff2", error:"net::ERR_CONNECTION_REFUSED" },
    { method:"POST", url:"http://127.0.0.1:49000/assets/font.woff2", error:"net::ERR_ABORTED" },
  ]) assert.equal(isExpectedQaRequestAbort(failure,"normal",site), false);
});
