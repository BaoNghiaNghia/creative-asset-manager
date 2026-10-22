// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillManagerModal } from "./SkillManagerModal";
import { ActionToastViewport } from "../components/ActionToast";
import { RrugcApiError, createStage2Skill, listStage2SkillRegistry, restoreArchivedStage1Skill, uploadLocalKeywordSkillForStage1, updateStageSkillDefault } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  listStage2SkillRegistry: vi.fn(async () => ({
    can_manage: true,
    stage_defaults: { stage2: "local:scale-image-8869-13-mau" },
    items: [
      {
        id: "keyword", source: "local", skill_id: null, skill_name: "gatorhats-keyword-embroidery",
        display_name: "GatorHats · Keyword Embroidery", description: "Artwork without reference images.",
        note: "Text-only generation for Stage 1", workflow: "image_studio", enabled: false,
        default_version: null, latest_version: null, synced_version: null,
        sync_state: "ready", validation_status: "valid", bundle_sha256: null,
        last_error: null, versions: [], created_at: "", updated_at: "",
      },
      {
        id: "scale", source: "local", skill_id: null, skill_name: "scale-image-8869-13-mau",
        display_name: "Scale Image 8869", description: "Apply artwork to all cap colors.",
        note: "Thirteen colorways", workflow: "image_studio", enabled: true,
        default_version: null, latest_version: null, synced_version: null,
        sync_state: "ready", validation_status: "valid", bundle_sha256: null,
        last_error: null, versions: [], created_at: "", updated_at: "",
      },
    ],
  })),
  listStage2Skills: vi.fn(async () => ({
    items: [{ source: "local", skill_id: null, skill_name: "scale-image-8869-13-mau", ready: true, keyword_artwork_ready: false }],
  })),
  createStage2Skill: vi.fn(async () => ({})), createStage2SkillVersion: vi.fn(), deleteStage2Skill: vi.fn(),
  deleteStage2SkillVersion: vi.fn(), setStage2SkillDefaultVersion: vi.fn(),
  setStage2SkillEnabled: vi.fn(), restoreArchivedStage1Skill: vi.fn(async () => ({})),
  uploadLocalKeywordSkillForStage1: vi.fn(async () => ({})), updateStage2SkillNote: vi.fn(), updateStageSkillDefault: vi.fn(),
  syncStage2SkillRegistry: vi.fn(),
}));

afterEach(() => { document.body.replaceChildren(); });

async function openLibrary(host: HTMLElement) {
  const tab = host.querySelector<HTMLButtonElement>('#rrugc-skill-tab-library');
  expect(tab).not.toBeNull();
  await act(async () => { tab!.click(); });
  expect(tab?.getAttribute('aria-selected')).toBe('true');
}


describe("Upload-time Skill versions", () => {
  it("shows hh_mm_dd_mm_yy as the release version but retains runtime IDs for controls", async () => {
    vi.mocked(listStage2SkillRegistry).mockResolvedValueOnce({
      can_manage: true,
      stage_defaults: {},
      items: [{
        id: "time-based-skill", source: "local", skill_id: null,
        skill_name: "gatorhats-stage1-six-designs",
        display_name: "GatorHats Stage 1", description: "",
        note: "", workflow: "image_studio", enabled: true,
        default_version: "1.2.0", latest_version: "1.2.0", synced_version: "1.2.0",
        default_upload_version: "18_39_12_10_26",
        latest_upload_version: "18_39_12_10_26",
        synced_upload_version: "18_39_12_10_26",
        sync_state: "ready", validation_status: "valid",
        bundle_sha256: null, last_error: null,
        versions: [{id:"v1", version:"1.2.0", upload_version:"18_39_12_10_26",
          status:"available", is_default:true, is_synced:true, bundle_sha256:null,
          created_at:"2026-10-12T11:39:00Z"}],
        created_at: "", updated_at: "",
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openLibrary(host);
    expect(host.querySelector(".rrugc-skill-compact-subtitle")?.textContent).toContain("v18_39_12_10_26");
    const details = host.querySelector<HTMLButtonElement>(".rrugc-skill-details-toggle")!;
    await act(async () => details.click());
    const select = host.querySelector<HTMLSelectElement>(".rrugc-skill-version-panel select")!;
    expect(select.options[0].textContent).toContain("v18_39_12_10_26");
    expect(select.options[0].value).toBe("1.2.0");
    expect(host.querySelector(".rrugc-skill-meta")?.textContent).toContain("18_39_12_10_26");
    await act(async () => root.unmount());
  });
});

describe("SkillManagerModal compact redesign", () => {
  it("shows compact cards, compatible Stage 1 defaults, expandable details and upload", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(host.querySelector(".rrugc-skill-summary-role")?.textContent).toBe("Manage access");
    expect(host.querySelector('select[aria-label="GatorHats Products Stage 1 default skill"]')?.querySelectorAll("option")).toHaveLength(2);
    expect(host.querySelector('select[aria-label="GatorHats Products Stage 2 Skill 7"]')?.querySelectorAll("option")).toHaveLength(2);
    await openLibrary(host);
    expect(host.querySelectorAll(".rrugc-skill-card")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-skill-delete-icon")).toHaveLength(2);
    expect(host.querySelector('button[aria-label="Delete skill GatorHats · Keyword Embroidery"]')).not.toBeNull();
    expect(host.querySelector(".rrugc-skill-details")).toBeNull();
    expect(host.textContent).toContain("GatorHats · Keyword Embroidery");
    expect(host.textContent).not.toContain("Stage 1 Skill is archived");
    expect(host.querySelector(".rrugc-skill-upload")).toBeNull();

    const keywordCard = [...host.querySelectorAll(".rrugc-skill-card")].find(card => card.textContent?.includes("GatorHats"));
    expect(keywordCard?.textContent).toContain("Text-only generation for Stage 1");
    await act(async () => { (keywordCard?.querySelector(".rrugc-skill-details-toggle") as HTMLButtonElement).click(); });
    expect(keywordCard?.querySelector(".rrugc-skill-details")).not.toBeNull();
    expect(keywordCard?.querySelector("textarea")).not.toBeNull();

    const uploadButton = host.querySelector(".rrugc-skill-add-toggle") as HTMLButtonElement;
    await act(async () => uploadButton.click());
    expect(host.querySelector(".rrugc-skill-upload")).not.toBeNull();

    const search = host.querySelector('input[aria-label="Search installed skills"]') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(search, "scale");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(search.value).toBe("scale");
    expect(host.querySelectorAll(".rrugc-skill-card")).toHaveLength(1);
    expect(host.querySelector(".rrugc-skill-card")?.textContent).toContain("Scale Image 8869");
    await act(async () => root.unmount());
  });
});
describe("Archived Stage 1 skill recovery", () => {
  it("offers an explicit authenticated restore action rather than instructing users to enable a hidden skill", async () => {
    vi.mocked(listStage2SkillRegistry).mockResolvedValueOnce({
      can_manage: true,
      stage_defaults: {},
      items: [],
      archived_items: [{
        id: "archived-keyword", source: "local", skill_id: null,
        skill_name: "gatorhats-keyword-embroidery",
        display_name: "GatorHats · Keyword Embroidery", description: "Keyword-only Skill",
        note: "", workflow: "image_studio", enabled: false,
        default_version: null, latest_version: null, synced_version: null,
        sync_state: "ready", validation_status: "valid", bundle_sha256: null,
        last_error: null, versions: [], created_at: "", updated_at: "",
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("Stage 1 Skill is archived");
    const restore = [...host.querySelectorAll("button")].find(button => button.textContent?.includes("Restore & set Stage 1"));
    expect(restore?.disabled).toBe(false);
    await act(async () => { restore?.click(); await Promise.resolve(); await Promise.resolve(); });
    expect(restoreArchivedStage1Skill).toHaveBeenCalledWith("archived-keyword");
    await act(async () => root.unmount());
  });
});

describe("Existing local redesign skill Stage 1 setup", () => {
  it("uploads the compatible ZIP for the existing local Skill rather than creating a duplicate", async () => {
    vi.mocked(listStage2SkillRegistry).mockResolvedValueOnce({
      can_manage: true, stage_defaults: { stage1: "local:gatorhats-keyword-embroidery" },
      items: [{
        id: "redesign", source: "local", skill_id: null, skill_name: "redesign-8869-v3",
        display_name: "Redesign 8869 V3", description: "Ten concepts and one hero",
        note: "", workflow: "image_studio", enabled: true,
        default_version: null, latest_version: null, synced_version: null,
        sync_state: "ready", validation_status: "valid", bundle_sha256: null,
        last_error: null, versions: [], created_at: "", updated_at: "",
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openLibrary(host);
    const details = host.querySelector<HTMLButtonElement>(".rrugc-skill-details-toggle")!;
    await act(async () => details.click());
    const input = host.querySelector<HTMLInputElement>(".rrugc-stage1-skill-update input[type=file]")!;
    expect(input).not.toBeNull();
    const upload = host.querySelector<HTMLButtonElement>(".rrugc-stage1-skill-update button")!;
    expect(upload.disabled).toBe(true);
    const file = new File(["Stage 1-ready content"], "redesign-8869-v3-stage1-ready.zip", { type: "application/zip" });
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    expect(upload.disabled).toBe(false);
    await act(async () => { upload.click(); await Promise.resolve(); await Promise.resolve(); });
    expect(uploadLocalKeywordSkillForStage1).toHaveBeenCalledWith("redesign", file);
    await act(async () => root.unmount());
  });
});

describe("Stage 1 shared Skill defaults", () => {
  it("offers hanh-redesign-8869-ver-3 and saves it as the Stage 1 default", async () => {
    vi.mocked(listStage2SkillRegistry).mockResolvedValueOnce({
      can_manage: true,
      stage_defaults: {},
      items: [{
        id: "hanh", source: "local", skill_id: null,
        skill_name: "hanh-redesign-8869-ver-3",
        display_name: "hanh-redesign-8869-ver-3", description: "Generate hat concepts",
        note: "", workflow: "image_studio", enabled: true,
        default_version: null, latest_version: null, synced_version: null,
        sync_state: "ready", validation_status: "valid", bundle_sha256: null,
        last_error: null, versions: [], created_at: "", updated_at: "",
      }],
    });
    vi.mocked(updateStageSkillDefault).mockResolvedValueOnce({ stage_defaults: {
      stage1: "local:hanh-redesign-8869-ver-3",
    } } as never);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="GatorHats Products Stage 1 default skill"]')!;
    expect([...select.options].map(option => option.textContent)).toContain("hanh-redesign-8869-ver-3");
    await act(async () => {
      select.value = "hanh";
      select.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    expect(updateStageSkillDefault).toHaveBeenCalledWith("prod_s1", "hanh");
    await act(async () => root.unmount());
  });
});

describe("Duplicate local Skill ZIP upload", () => {
  it("asks before replacing an existing Skill and retries only after confirmation", async () => {
    vi.mocked(createStage2Skill).mockReset();
    vi.mocked(createStage2Skill)
      .mockRejectedValueOnce(new RrugcApiError(409, "Already exists", "stage2_skill_already_exists"))
      .mockResolvedValueOnce({} as never);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<><ActionToastViewport /><SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} /></>));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openLibrary(host);
    await act(async () => host.querySelector<HTMLButtonElement>(".rrugc-skill-add-toggle")!.click());
    const input = host.querySelector<HTMLInputElement>(".rrugc-skill-upload input[type=file]")!;
    const file = new File(["updated bundle"], "existing-studio.zip", { type: "application/zip" });
    Object.defineProperty(input, "files", { configurable: true, value: [file] });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    await act(async () => { host.querySelector<HTMLButtonElement>(".rrugc-skill-upload .rrugc-primary")!.click(); });
    expect(confirm).toHaveBeenCalledOnce();
    expect(createStage2Skill).toHaveBeenNthCalledWith(1, file);
    expect(createStage2Skill).toHaveBeenNthCalledWith(2, file, true);
    expect(host.querySelector(".cam-action-toast--success")?.textContent).toContain("Existing Skill replaced");
    confirm.mockRestore();
    await act(async () => root.unmount());
  });

  it("does not replace a duplicate when the confirmation is cancelled", async () => {
    vi.mocked(createStage2Skill).mockReset().mockRejectedValueOnce(
      new RrugcApiError(409, "Already exists", "stage2_skill_already_exists"),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openLibrary(host);
    await act(async () => host.querySelector<HTMLButtonElement>(".rrugc-skill-add-toggle")!.click());
    const input = host.querySelector<HTMLInputElement>(".rrugc-skill-upload input[type=file]")!;
    Object.defineProperty(input, "files", {
      configurable: true,
      value: [new File(["bundle"], "existing-studio.zip", { type: "application/zip" })],
    });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    await act(async () => { host.querySelector<HTMLButtonElement>(".rrugc-skill-upload .rrugc-primary")!.click(); });
    expect(confirm).toHaveBeenCalledOnce();
    expect(createStage2Skill).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
    await act(async () => root.unmount());
  });
});

describe("GatorHats workflow-specific Skill Manager", () => {
  it("shows seven distinct Product Stage 2 slots, without shared Review selectors", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal workflow="products" open
      onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("Manage Skills · GatorHats");
    expect(host.querySelector('#rrugc-skill-tab-products')?.getAttribute('aria-selected')).toBe('true');
    expect(host.querySelectorAll("[data-stage2-slot]")).toHaveLength(7);
    expect(host.textContent).toContain("Assignment only · Photo worker not enabled");
    for (const image of ["Image 01", "Image 02", "Image 03", "Image 04"]) {
      expect(host.textContent).toContain("Ecommerce · " + image);
    }
    expect(host.textContent).toContain("Skill 5");
    expect(host.textContent).toContain("Multi-hat");
    expect(host.textContent).toContain("Skill 6");
    expect(host.textContent).toContain("UGC");
    expect(host.textContent).toContain("Skill 7");
    expect(host.querySelector('select[aria-label="GatorHats Reviews Stage 2 default skill"]')).toBeNull();
    const colorway = host.querySelector<HTMLSelectElement>(
      'select[aria-label="GatorHats Products Stage 2 Skill 7"]',
    );
    expect(colorway?.value).toBe("scale"); // existing legacy default migrates to Products only as read
    await act(async () => root.unmount());
  });

  it("keeps the Reviews Skill Manager separate from Product's seven slots", async () => {
    vi.mocked(updateStageSkillDefault).mockClear();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal workflow="reviews" open
      onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("Manage Skills · GatorHats");
    expect(host.querySelector('#rrugc-skill-tab-reviews')?.getAttribute('aria-selected')).toBe('true');
    expect(host.querySelectorAll("[data-stage2-slot]")).toHaveLength(0);
    expect(host.querySelector('select[aria-label="GatorHats Products Stage 2 Skill 4"]')).toBeNull();
    const reviewSelect = host.querySelector<HTMLSelectElement>(
      'select[aria-label="GatorHats Reviews Stage 2 default skill"]',
    );
    expect(reviewSelect?.value).toBe("scale");
    await act(async () => {
      reviewSelect!.value = "";
      reviewSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    expect(updateStageSkillDefault).toHaveBeenCalledWith("rev_s2", null);
    await act(async () => root.unmount());
  });
});
describe("Unified GatorHats Manage Skills tabs", () => {
  it("switches Products, Reviews and the one shared library without reopening the modal", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal workflow="products" open
      onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(host.querySelectorAll('[role="tab"]')).toHaveLength(3);
    expect(host.querySelectorAll("[data-stage2-slot]")).toHaveLength(7);
    expect(host.querySelectorAll(".rrugc-skill-card")).toHaveLength(0);

    await act(async () => { (host.querySelector("#rrugc-skill-tab-reviews") as HTMLButtonElement).click(); });
    expect(host.querySelector("#rrugc-skill-tab-reviews")?.getAttribute("aria-selected")).toBe("true");
    expect(host.querySelector('select[aria-label="GatorHats Reviews Stage 2 default skill"]')).not.toBeNull();
    expect(host.querySelectorAll("[data-stage2-slot]")).toHaveLength(0);

    await openLibrary(host);
    expect(host.querySelectorAll(".rrugc-skill-card")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-skill-catalog-toolbar")).toHaveLength(1);
    expect(host.querySelectorAll("[data-stage2-slot]")).toHaveLength(0);

    await act(async () => { (host.querySelector("#rrugc-skill-tab-products") as HTMLButtonElement).click(); });
    expect(host.querySelectorAll("[data-stage2-slot]")).toHaveLength(7);
    expect(host.querySelectorAll(".rrugc-skill-card")).toHaveLength(0);
    expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await act(async () => root.unmount());
  });
});
