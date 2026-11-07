import { firefox } from "playwright";

const pageUrl = process.argv[2] || "https://creative-assets.ddns.net/privacy-policy";
const imageUrls = process.argv.slice(3);
if (!imageUrls.length) {
  throw new Error("Usage: node scripts/verify-etsy-image-render.mjs <CAM-public-url> <Etsy-thumb-url> [...]");
}

const browser = await firefox.launch({headless: true});
try {
  const page = await browser.newPage();
  const pageResponse = await page.goto(pageUrl, {waitUntil: "domcontentloaded", timeout: 30000});
  if (!pageResponse || !pageResponse.ok()) {
    throw new Error("CAM public route unavailable: " + (pageResponse?.status() ?? "unknown"));
  }

  const csp = (await pageResponse.headerValue("content-security-policy")) || "";
  const required = ["https://i.etsystatic.com", "https://img.etsystatic.com"];
  const imgSource = csp.split(";").find(p => p.trim().startsWith("img-src ")) || "";
  if (required.some(origin => !imgSource.split(/\s+/).includes(origin))) {
    throw new Error("Production CSP does not allow both Etsy image CDN origins");
  }

  const outcomes = await page.evaluate(async urls => {
    return Promise.all(urls.map(src => new Promise(resolve => {
      const img = document.createElement("img");
      let completed = false;
      const finish = outcome => {
        if (completed) return;
        completed = true;
        img.remove();
        resolve(outcome);
      };
      img.onload = () => finish({
        loaded: true, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight,
      });
      img.onerror = () => finish({loaded: false, error: "image load error"});
      document.body.append(img);
      img.src = src;
      setTimeout(() => finish({loaded: false, error: "timeout"}), 16000);
    })));
  }, imageUrls);

  for (let i = 0; i < outcomes.length; i++) {
    const outcome = outcomes[i];
    console.log(`Etsy thumbnail ${i + 1}: ${JSON.stringify(outcome)}`);
    if (!outcome.loaded || !outcome.naturalWidth || !outcome.naturalHeight) {
      throw new Error(`Etsy thumbnail ${i + 1} failed to render in production browser`);
    }
  }
  console.log("PASS: CAM production browser loads Etsy thumbnail images directly from CDN.");
} finally {
  await browser.close();
}
