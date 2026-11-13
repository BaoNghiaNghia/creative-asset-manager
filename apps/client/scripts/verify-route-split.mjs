// Phase 7: guard the startup asset budget and route CSS boundaries.
// This runs against the built Vite dist, not a development server.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const dist = new URL("../dist/", import.meta.url);
const assets = new URL("./assets/", dist);
const html = readFileSync(new URL("index.html", dist), "utf8");
const files = readdirSync(assets.pathname);
const jsName = html.match(/\/assets\/(index-[\w-]+\.js)/)?.[1];
const cssName = html.match(/\/assets\/(index-[\w-]+\.css)/)?.[1];
if (!jsName || !cssName) throw new Error("Cannot locate Vite entry JS/CSS.");
const readAsset = name => readFileSync(join(assets.pathname, name));
const gzipBytes = name => gzipSync(readAsset(name)).length;
const indexJs = gzipBytes(jsName);
const indexCss = gzipBytes(cssName);
const hasChunk = pattern => files.some(name => pattern.test(name));
const routeCss = files.filter(name => name.endsWith(".css") && name !== cssName)
  .map(name => ({ name, text: readAsset(name).toString("utf8") }));
const reviewCss = routeCss.find(asset => asset.text.includes(".review-board-workspace"));
const publicCss = routeCss.find(asset => asset.text.includes(".public-review-feed"));
const mainCss = readAsset(cssName).toString("utf8");
const maxIndexJs = 90_000; // baseline pre-split was 182 KB gzip
const maxIndexCss = 115_000; // baseline pre-split was 123 KB gzip

const failures = [
  !hasChunk(/^App-[\w-]+\.js$/) ? "Asset Explorer must be a lazy JS chunk" : "",
  !reviewCss ? "Review Board must own its route CSS chunk" : "",
  !publicCss ? "Public Review must own its route CSS chunk" : "",
  mainCss.includes(".public-review-feed") ? "Public Review CSS leaked into startup" : "",
  indexJs > maxIndexJs ? `Startup JS ${indexJs} exceeds ${maxIndexJs} gzip bytes` : "",
  indexCss > maxIndexCss ? `Startup CSS ${indexCss} exceeds ${maxIndexCss} gzip bytes` : "",
].filter(Boolean);

const kb = bytes => (bytes / 1000).toFixed(1);
console.log(
  `Startup JS: ${kb(indexJs)} KB gzip (${jsName}) | budget ${kb(maxIndexJs)} KB\n` +
  `Startup CSS: ${kb(indexCss)} KB gzip (${cssName}) | budget ${kb(maxIndexCss)} KB\n` +
  `Dynamic chunks: Explorer=${hasChunk(/^App-[\w-]+\.js$/)} Review=${reviewCss?.name || "missing"} Public=${publicCss?.name || "missing"}`
);
if (failures.length) {
  for (const failure of failures) console.error("FAIL:", failure);
  process.exitCode = 1;
}
