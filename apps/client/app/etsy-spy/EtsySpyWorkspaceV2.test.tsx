import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { Listing } from "./api";
import {
  EtsySpyResultsToolbar, EtsySpyQuickInspect, OPTIONAL_SPY_COLUMNS, safeSpyPreview,
} from "./EtsySpyWorkspaceV2";

const sample: Listing = {
  sessionId:"session", keyword:"dinosaur", sourceKind:"etsy", listingKey:"123456789",
  picked:true, pageTitle:"Dinosaur Didn't Read Shirt", url:"https://www.etsy.com/listing/123456789",
  badge:"Bestseller", searchBadge:null, bought24h:null, views24h:12, inCarts:20,
  demandScore:null, observedAt:"2026-11-22T00:00:00Z",
  tm:"unknown", tmStatus:"unknown", tmSource:null, tmCheckedAt:null, tmSourceAsOf:null,
  thumbnailUrl:"https://i.etsystatic.com/123/il.jpg",
  tiktokListing:{status:"completed", draft_only:true,
    search_terms_preview:["dinosaur shirt","funny dinosaur tee"]},
  listingImages:[{garmentCode:"C1717",count:10,mainThumbnailUrl:"/api/v1/etsy-spy/images/main"}],
  videoUgc:{count:1,status:"ready",videos:[]},
  keywordSms:["dinosaur tee"],
};

describe("Etsy Spy V2 product intelligence", () => {
  it("renders a permission-neutral, scoped toolbar with real column switches", () => {
    const html = renderToStaticMarkup(<EtsySpyResultsToolbar
      scope="picked" counts={{eligible:703,picked:353,later:855}}
      onScope={() => undefined} columns={[{id:"pageTitle",text:"Product / Etsy"},
        {id:"design",text:"Design"},{id:"videoUgc",text:"Video UGC"}]}
      hiddenColumns={["videoUgc"]} onToggleColumn={() => undefined}
      density="compact" onDensity={() => undefined} onToggleFilters={() => undefined}
      filtersExpanded={false} activeFilters={["Ngày: Hôm nay"]} onClearFilters={() => undefined}
      resultCount={353} inspectorOpen={false} />);
    expect(html).toContain("703");
    expect(html).toContain("353");
    expect(html).toContain("aria-pressed=\"true\"");
    expect(html).toContain("Video UGC");
    expect(html).not.toContain("Product / Etsy</span>");
    expect(html).toContain("Ngày: Hôm nay");
    expect(html).toContain("Mật độ Gọn");
    expect(OPTIONAL_SPY_COLUMNS.has("pageTitle")).toBe(false);
  });

  it("renders Quick Inspect from existing, read-only listing data", () => {
    const html = renderToStaticMarkup(<EtsySpyQuickInspect listing={sample}
      canManage={false} busy={false} onClose={() => undefined}
      onPicked={() => undefined} onLater={() => undefined} />);
    expect(html).toContain("Quick Inspect");
    expect(html).toContain("Dinosaur Didn");
    expect(html).toContain("dinosaur shirt");
    expect(html).toContain("10");
    expect(html).toContain("Video UGC");
    expect(html).toContain("20+");
    expect(html).toContain("disabled");
  });

  it("rejects non-HTTP previews and protocol-relative URLs", () => {
    expect(safeSpyPreview("javascript:alert(1)")).toBeNull();
    expect(safeSpyPreview("//outside.com/img.jpg")).toBeNull();
    expect(safeSpyPreview("http://outside.com/img.jpg")).toBeNull();
    expect(safeSpyPreview("https://outside.com/img.jpg")).toBeNull();
    expect(safeSpyPreview("/api/v1/image.png")).toBe("/api/v1/image.png");
    expect(safeSpyPreview("https://i.etsystatic.com/image.jpg")).toContain("etsystatic.com");
  });
});
