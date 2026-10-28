/* Typography Engine V2 prototype screenshot QA. Run after npm ci in apps/client:
 *   node docs/operations/prototypes/capture_qa.cjs
 * This checks the static design reference only, NOT the production UI.
 */
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');
const root = __dirname;
const html = path.join(root, 'CAM_TYPOGRAPHY_ENGINE_V2_UI_PREVIEW.html');
const destination = path.join(root, 'screenshots');
const cases = [
  ['ui_stage1_jobs_desktop.png', 'jobs', 1440, 900],
  ['ui_ideas_desktop.png', 'ideas', 1440, 900],
  ['ui_lab_desktop.png', 'lab', 1440, 900],
  ['ui_lab_mobile.png', 'lab', 390, 844],
  ['ui_windows_desktop.png', 'windows', 1440, 900]
];
(async () => {
  fs.mkdirSync(destination, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const failures = [];
  try {
    for (const [filename, view, width, height] of cases) {
      const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
      page.on('pageerror', e => failures.push(filename + ': JS ' + String(e)));
      await page.goto(pathToFileURL(html).href, { waitUntil: 'load' });
      if (view === 'windows') {
        await page.locator('#toolPreviewButton').click();
      } else if (view !== 'jobs') {
        await page.locator('.view-tab[data-view="' + view + '"]').click();
      }
      await page.waitForTimeout(80);
      const info = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        active: [...document.querySelectorAll('.view.active')].map(x => x.id)
      }));
      if (info.overflow || !info.active.includes('view-' + view)) {
        failures.push(filename + ': ' + JSON.stringify(info));
      }
      await page.screenshot({ path: path.join(destination, filename), fullPage: true });
      console.log(filename + ': ' + (info.overflow ? 'OVERFLOW' : 'ok') + ', ' + info.active.join(','));
      await page.close();
    }
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(failures.join('\n'));
    process.exitCode = 1;
  }
})().catch(e => { console.error(e); process.exitCode = 1; });