// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillManagerModal } from "./SkillManagerModal";
import { listStage2SkillRegistry, restoreArchivedStage1Skill } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", () => ({
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
  createStage2Skill: vi.fn(), createStage2SkillVersion: vi.fn(), deleteStage2Skill: vi.fn(),
  deleteStage2SkillVersion: vi.fn(), setStage2SkillDefaultVersion: vi.fn(),
  setStage2SkillEnabled: vi.fn(), restoreArchivedStage1Skill: vi.fn(async () => ({})), updateStage2SkillNote: vi.fn(), updateStageSkillDefault: vi.fn(),
  syncStage2SkillRegistry: vi.fn(),
}));

afterEach(() => { document.body.replaceChildren(); });

describe("SkillManagerModal compact redesign", () => {
  it("shows compact cards, compatible Stage 1 defaults, expandable details and upload", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillManagerModal open onClose={() => undefined} onChanged={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(host.querySelectorAll(".rrugc-skill-card")).toHaveLength(2);
    expect(host.querySelector(".rrugc-skill-details")).toBeNull();
    expect(host.textContent).toContain("GatorHats · Keyword Embroidery");
    expect(host.textContent).toContain("Stage 1 requires a keyword-only Skill");
    expect(host.querySelector('select[aria-label="Stage 1 default skill"]')?.querySelectorAll("option")).toHaveLength(1);
    expect(host.querySelector('select[aria-label="Stage 2 default skill"]')?.querySelectorAll("option")).toHaveLength(2);
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
