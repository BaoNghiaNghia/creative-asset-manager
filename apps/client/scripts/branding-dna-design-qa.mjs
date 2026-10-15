import { firefox } from "playwright";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve, dirname } from "node:path";
import { mkdir } from "node:fs/promises";

const client = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const file = resolve(client, "public/branding-dna-preview/index.html");
const screenshots = resolve(client, "../../docs/features/branding-dna/screenshots");
await mkdir(screenshots, {recursive: true});
const checks = [];
const browser = await firefox.launch({headless:true});
try {
  for(const view of [
    {name:"desktop",width:1440,height:900},
    {name:"tablet",width:768,height:1024},
    {name:"mobile",width:390,height:844},
    {name:"small-mobile",width:320,height:700},
  ]) {
    const page=await browser.newPage({viewport:{width:view.width,height:view.height}});
    const errors=[]; page.on("pageerror",e=>errors.push(e.message));
    const url=pathToFileURL(file).href+"?embed=1&view=overview";
    await page.goto(url,{waitUntil:"load"});
    await page.locator('[data-view="shops"].tab').waitFor({timeout:10000});
    await page.waitForTimeout(100);
    const width=await page.evaluate(()=>document.documentElement.scrollWidth);
    if(width>view.width+1) throw Error(view.name+": horizontal overflow "+width+" > "+view.width);
    const sidebarHidden=await page.locator(".sidebar").evaluate(el=>getComputedStyle(el).display==="none");
    if(!sidebarHidden) throw Error(view.name+": embedded mode duplicates CAM sidebar");
    await page.locator('[data-view="shops"].tab').click();
    if(!(await page.locator('[data-view="shops"].tab').getAttribute("aria-selected")).includes("true")) throw Error(view.name+": Shop Library tab didn't activate");
    await page.locator('[data-view="scanner"].tab').click();
    await page.locator('[data-view="products"].tab').click();
    await page.locator('[data-view="dna"].tab').click();
    await page.locator('[data-view="overview"].tab').click();
    if(view.name==="desktop"){
      await page.screenshot({path:resolve(screenshots,"desktop-overview.png"),fullPage:true});
      await page.locator('[data-action="add-shop"]').first().click();
      if(!(await page.locator("#modal-root .modal").count())) throw Error("Add Shop wizard not visible");
      await page.screenshot({path:resolve(screenshots,"desktop-add-shop.png"),fullPage:true});
    }
    if(view.name==="mobile")await page.screenshot({path:resolve(screenshots,"mobile-overview.png"),fullPage:true});
    if(errors.length)throw Error(view.name+": JS errors: "+errors.join("; "));
    checks.push({viewport:view.name,passed:true});
    await page.close();
  }
  console.log("Branding DNA design QA PASSED",JSON.stringify(checks));
}finally{await browser.close();}
