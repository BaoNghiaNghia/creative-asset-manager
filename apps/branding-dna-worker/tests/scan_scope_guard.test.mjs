import test from "node:test";
import assert from "node:assert/strict";
import {FUTURE_SCAN_LIMITS,inspectCandidateUrlScope} from "../src/scan_scope_guard.mjs";

const etsy = "https://www.etsy.com/shop/TestHats";
const amazon = "https://www.amazon.com/stores/EMBROLY/page/ABC-1";
const match = (platform,base,url) =>
  inspectCandidateUrlScope(platform,base,url).withinScope;

test("only the exact registered storefront and two descendants remain in scope",()=>{
  for(const url of [etsy,etsy+"/item",etsy+"/item/second"]){
    assert.equal(match("etsy",etsy,url),true);
  }
  assert.equal(match("amazon",amazon,amazon),true);
  assert.equal(match("amazon",amazon,amazon+"/listing"),true);
  assert.equal(match("amazon",amazon,amazon+"/listing/variant"),true);
  assert.equal(match("amazon",amazon,amazon+"/listing/variant/more"),false);
  assert.equal(FUTURE_SCAN_LIMITS.requestAllowed,false);
  assert.equal(FUTURE_SCAN_LIMITS.maxRedirects,0);
  assert.equal(FUTURE_SCAN_LIMITS.maxPages,5);
  assert.equal(FUTURE_SCAN_LIMITS.maxGalleryItems,20);
});

test("refuses host tricks, dot navigation, ports, URL parameters and unsupported shops",()=>{
  const attacks=[
    "http://www.etsy.com/shop/TestHats",
    "https://www.etsy.com:443/shop/TestHats",
    "https://etsy.com/shop/TestHats",
    "https://www.etsy.com.shop.example/shop/TestHats",
    "https://www.etsy.com@evil.test/shop/TestHats",
    "https://www.etsy.com/shop/TestHats?sort=1",
    "https://www.etsy.com/shop/TestHats#",
    "https://www.etsy.com/shop/TestHats/%2E%2e",
    "https://www.etsy.com/shop/TestHats/../Other",
    "https://www.etsy.com/shop/TestHats/./item",
    "https://www.etsy.com/shop/TestHats//item",
    "https://www.etsy.com/shop/TestHats/one/two/three",
    "https://www.etsy.com/shop/TestHatsOthers",
    "https://www.etsy.com/shop/Other",
    "https://www.etsy.com/shop/TestHats;cmd",
    "https://www.etsy.com/shop/TestHats\\other",
    "https://www.etsy.com/shop/TestHats\nInjected: 1",
    "https://ｗｗｗ.etsy.com/shop/TestHats",
    "http://169.254.169.254/latest/meta-data",
    "file:///etc/passwd",
  ];
  for(const url of attacks){
    assert.equal(match("etsy",etsy,url),false,url);
  }
  assert.equal(match("amazon","https://example.com/stores/Test",amazon),false);
  assert.equal(match("shopify","https://abc.myshopify.com/products",etsy),false);
  assert.equal(match("amazon",amazon,"https://www.amazon.com/dp/12345"),false);
  assert.equal(match("amazon",amazon,"https://www.amazon.com/stores/EMBROLY/page/OTHER"),false);
  assert.equal(match("amazon",amazon,"https://www.amazon.ca/stores/EMBROLY/page/ABC-1"),false);
});

test("returns reason only, never an executable browser or navigation instruction",()=>{
  const accepted=inspectCandidateUrlScope("etsy",etsy,etsy+"/items");
  assert.deepEqual(accepted,{withinScope:true,reason:"registered_storefront_subpath"});
  assert.equal("url" in accepted,false);
  assert.equal("job" in accepted,false);
  const rejected=inspectCandidateUrlScope("etsy",etsy,
    "https://www.etsy.com/shop/Other/items");
  assert.equal(rejected.reason,"candidate_path_outside_shop");
});
