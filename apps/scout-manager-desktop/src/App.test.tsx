import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "./App";
import { BrandingDnaReports } from "./BrandingDnaReports";
import {agentDisplayState,blockedShopSummary,camConnectionState,describeShopBlockers} from "./BrandingDnaPilotCard";
describe("Tauri Scout Manager dashboard", () => {
  it("keeps CAM connected even when no shops have been configured",()=>{
    expect(camConnectionState(true,[])).toEqual({connected:true,shopCount:0});
    expect(camConnectionState(false,[])).toEqual({connected:false,shopCount:null});
    expect(camConnectionState(true,[{id:"a",displayName:"Shop",platform:"amazon",
      shopUrl:"https://www.amazon.com/stores/Test",status:"draft",accessMode:"public_observation"}]))
      .toEqual({connected:true,shopCount:1});
  });
  it("shows per-shop preflight blockers without implying scanning is enabled",()=>{
    expect(describeShopBlockers(["scan_task_dispatch_not_deployed","etsy_written_permission_not_verified"])).toContain("CAM chưa cấp job");
  });
  it("distinguishes standby from actual scanning and shows specific CAM blockers",()=>{
    expect(agentDisplayState(true,false)).toBe("Agent standby · 0 scan jobs");
    expect(agentDisplayState(false,false)).toBe("Agent stopped");
    const summary=blockedShopSummary([
      {shopId:"a",platform:"amazon",displayName:"A",status:"blocked",stagedTasks:0,
       blockers:["shop_not_active","shop_access_not_declared","no_staged_scan_tasks","marketplace_authorization_not_verified"]},
      {shopId:"b",platform:"etsy",displayName:"B",status:"blocked",stagedTasks:0,
       blockers:["shop_not_active","no_staged_scan_tasks","marketplace_authorization_not_verified"]},
    ]);
    expect(summary).toEqual({paused:0,withoutTasks:2,awaitingSourcePermission:2});
  });
  it("renders both pipelines and monitoring UI without enabling native controls in browser preview", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("RRUGC Scout Manager");
    expect(html).toContain("Manager v");
    expect(html).toContain("Scout version");
    expect(html).toContain("Review Scout");
    expect(html).toContain("Keyword Scout");
    expect(html).toContain("Cap Stack Scout");
    expect(html).toContain("Typography Design Scout");
    expect(html).toContain("Stage 0 picked keywords");
    expect(html).toContain("Run once");
    expect(html).toContain("Find new designs");
    expect(html).toContain("Sign in to Pinterest");
    expect(html).toContain("Never part of Run automation");
    expect(html).toContain("Branding DNA · Browser Agent");
    expect(html).toContain("Mở shop tự do");
    expect(html).toContain("Start agent");
    expect(html).toContain("Stop agent");
    expect(html).toContain("Refresh CAM");
    expect(html).toContain("Shop từ CAM");
    expect(html).not.toContain("Select saved shop");
    expect(html).not.toContain("Add shop");
    expect(html).not.toContain("Authorization file missing");
    expect(html).toContain("Điều phối");
    expect(html).not.toContain("Manual pilot only");
    expect(html).not.toContain("Selected storefront");
    expect(html).toContain("Start chỉ kết nối và chờ");
    expect(html).toContain("Shared Cap Stack library");
    expect(html).toContain('class="scout-compact-kpis"');
    expect(html).toContain("Details &amp; diagnostics");
    expect(html).not.toContain("Advanced tools &amp; authorization");
    expect(html).not.toContain("Select shop from CAM");
    expect(html).not.toContain('class="branding-library-grid"');
    expect(html).toContain('branding-main-actions');
    expect(html).not.toContain("Stage 0 keywords");
    expect(html).toContain("Agent-Reach · VPS Auto Discover");
    expect(html).toContain("unaffected by Windows Run / Pause / Quit");
    expect(html).not.toContain("Pause VPS");
    expect(html).toContain("Total saved");
    expect(html).toContain("Added 24h");
    expect(html).toContain("Last saved");
    expect(html).toContain("Run automation");
    expect(html).toContain("Minimize Scout Chrome windows");
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain("Review Scout AI queue (shared Gemini)");
    expect(html).toContain("no ImageGen calls.");
    expect(html).toContain("Stage 1 AI jobs");
    expect(html).toContain("Keyword search jobs");
    expect(html).toContain("Queued / retry");
    expect(html).toContain("Processing");
    expect(html).toContain("Done · 24h");
    expect(html).toContain("Failed · 24h");
    expect(html).toContain("Eligible now");
    expect(html).toContain("Cooling down");
    expect(html).toContain("Last completed agent cycle");
    expect(html).toContain("Gemini");
    expect(html).toContain("Checking Gemini key pool");
    expect(html).toContain("Run automation");
    expect(html).not.toContain("Live activity");
    expect(html).not.toContain("> Pairing</button>");
    expect(html).not.toContain("> Update</button>");
    expect(html).not.toContain('class="summary"');
    expect(html).not.toContain('class="terminal-panel"');
    expect(html).toContain("Open log folder");
    expect(html).toContain("Branding DNA Agent");
    expect(html).toContain('disabled=""');
  });
  it("shows only a read-only local reports surface when a shop is selected", () => {
    const html = renderToStaticMarkup(<BrandingDnaReports nativeAvailable={false} shopUrl="https://www.etsy.com/shop/TestCaps" scanRunning={false}/>);
    expect(html).toContain("Local pilot results &amp; review");
    expect(html).toContain("Pick / Reject shortlist");
    expect(html).toContain("Prepare handoff (0)");
    expect(html).toContain("not uploaded to CAM");
    expect(html).toContain("No saved pilot results for this shop");
    expect(html).not.toContain("Download All");
  });
  it("does not interpolate Scout tokens or synthetic running PIDs into server markup", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).not.toContain("RRUGC_SCOUT_TOKEN=");
    expect(html).not.toContain("22628");
    expect(html).toContain("Stopped");
  });
});