// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from "vitest";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import type {DesignAsset, Listing} from "./api";
import {DesignCell} from "./DesignCell";

vi.mock("./api", async original => {
  const actual = await original<typeof import("./api")>();
  return {...actual, spyFetch: vi.fn()};
});
import {spyFetch} from "./api";
const fetchSpy = vi.mocked(spyFetch);
(globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT: boolean})
  .IS_REACT_ACT_ENVIRONMENT = true;

const oldJob = "00000000-1111-4222-8333-444444444444";
const newJob = "00000000-1111-4222-8333-555555555555";
const design = (job_id = oldJob, position = "front"): DesignAsset => ({
  id: "a".repeat(24), job_id, listing_key: "1234567890",
  position, variant: 1, method: "imagegen", status: "needs_review",
  source: "mapped", output_sha256: (job_id === oldJob ? "c" : "d").repeat(64),
  thumbnail_url: "/img/" + job_id + "/thumb",
  original_url: "/img/" + job_id + "/original",
  print_ready: false, rights_status: "unverified", manual_qa_required: true,
});
const listing = (designs: DesignAsset[] = [design()], status = "needs_review",
  job_id = oldJob): Listing => ({
  listingKey: "1234567890", picked: true, designs,
  pageTitle: "Joy to the World Sweatshirt",
  extraction: {status, job_id, manual_qa_required: true},
}) as Listing;

describe("Design modal retry latest extraction flow", () => {
  let root: Root | null = null;
  let host: HTMLDivElement | null = null;
  const open = async (record: Listing, canRetry = true, onRetryQueued = vi.fn()) => {
    host = document.createElement("div"); document.body.append(host);
    root = createRoot(host);
    await act(async () => root?.render(
      <DesignCell item={record} canRetry={canRetry}
        onRetryQueued={onRetryQueued}/>));
    await act(async () => host?.querySelector<HTMLButtonElement>(
      ".etsy-design-thumb")?.click());
    return onRetryQueued;
  };
  const button = () => document.body.querySelector<HTMLButtonElement>(
    ".etsy-design-gallery-extraction-retry");

  afterEach(async () => {
    await act(async () => root?.unmount());
    host?.remove(); root = null; host = null;
    document.body.style.overflow = "";
    fetchSpy.mockReset();
  });

  it("labels two same-front couple designs by garment group", async () => {
    const meta = {inventory_version: 3 as const, listing_type: "couple_set",
      design_evidence_id: "e".repeat(64), artwork_id: "D01",
      ink_variant_id: "I01", garment_group_id: "G01", set_id: "S01",
      view_side: "front", region: "front_center", observation_count: 1};
    const a = {...design(), inventory_v3: meta};
    const b = {...design(), id: "b".repeat(24), variant: 2,
      inventory_v3: {...meta, artwork_id: "D02", garment_group_id: "G02"}};
    await open(listing([a, b]), false);
    expect(document.body.querySelector(".etsy-design-gallery-eyebrow")?.textContent).toContain("Couple");
    const labels = Array.from(document.body.querySelectorAll(".etsy-design-gallery-variant-label")).map(x=>x.textContent);
    expect(labels).toEqual(["Áo A · Mẫu 1", "Áo B · Mẫu 2"]);
  });

  it("does not offer editing without manage permission", async () => {
    await open(listing(), false);
    expect(button()).toBeNull();
  });

  it("queues exactly the displayed SHA once and keeps the modal/old PNG while waiting", async () => {
    let accept!: (value: unknown) => void;
    fetchSpy.mockImplementation(() => new Promise(resolve => {accept = resolve;}));
    const onQueued = await open(listing());
    const gallery = document.body.querySelector(".etsy-design-gallery");
    expect(button()?.textContent).toContain("Tạo lại design");
    await act(async () => {button()?.click(); button()?.click();});
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe(
      "/listings/1234567890/extraction/retry");
    expect(fetchSpy.mock.calls[0][1]).toMatchObject({method: "POST"});
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]?.body))).toEqual({
      job_id: oldJob, asset_id: "a".repeat(24),
      output_sha256: "c".repeat(64),
    });
    expect(button()?.disabled).toBe(true);
    await act(async () => {
      accept({job_id: newJob, status: "pending", already_queued: false});
    });
    expect(onQueued).toHaveBeenCalledTimes(1);
    expect(button()?.textContent).toContain("Đang tách lại");
    expect(document.body.querySelector(".etsy-design-gallery")).toBe(gallery);
    expect(document.body.querySelector(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toContain(oldJob);

    await act(async () => root?.render(
      <DesignCell item={listing([design()], "processing", newJob)}
        canRetry onRetryQueued={onQueued}/>));
    expect(button()?.disabled).toBe(true);
    expect(document.body.querySelector(".etsy-design-gallery")).toBe(gallery);
    await act(async () => root?.render(
      <DesignCell item={listing([design(newJob)], "needs_review", newJob)}
        canRetry onRetryQueued={onQueued}/>));
    expect(document.body.querySelector(".etsy-design-gallery")).toBe(gallery);
    expect(document.body.querySelector(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toContain(newJob);
    expect(document.body.querySelector(".etsy-design-gallery-retry-feedback")
      ?.textContent).toContain("Đã nhận design mới");
    expect(button()?.disabled).toBe(false);
  });

  it("shows the failure in the dialog without dropping the current design", async () => {
    fetchSpy.mockRejectedValue(new Error("409: Another extraction is running"));
    await open(listing());
    await act(async () => {button()?.click();});
    expect(document.body.querySelector('[role="alert"]')?.textContent)
      .toContain("409");
    expect(document.body.querySelector(".etsy-design-gallery-stage img")
      ?.getAttribute("src")).toContain(oldJob);
    expect(button()?.disabled).toBe(false);
  });
});
