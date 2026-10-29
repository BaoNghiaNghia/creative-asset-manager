// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Stage1Subnav } from "./Stage1Subnav";
import { TypographyIdeasPage } from "./TypographyIdeasPage";
import {
  archiveTypographyReference, listTypographyDna, listTypographyReferences,
  patchTypographyReference, uploadTypographyReferences,
} from "./api";
import type { TypographyReference, TypographyReferencePage } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", () => ({
  listTypographyReferences: vi.fn(),
  getTypographyReference: vi.fn(),
  listTypographyDna: vi.fn(),
  uploadTypographyReferences: vi.fn(),
  patchTypographyReference: vi.fn(),
  archiveTypographyReference: vi.fn(),
}));

const reference: TypographyReference = {
  id: "ref-1", asset_id: "asset-1", filename: "Serif Script.png",
  content_hash: "d".repeat(64), source_type: "upload",
  source_url: null, source_platform: null, source_rights_status: "unknown",
  source_attribution: null, privacy: "private", scope: "workspace",
  campaign_id: null, keyword_id: null, skill_name: null,
  reference_roles: ["layout", "font_mood", "overlap"], influence: "balanced",
  preference: "neutral", priority: 0, tags: ["serif"], notes: "Natural vintage curves",
  analysis_status: "pending", editorial_status: "candidate", created_by_user_id: "owner",
  created_at: "2026-10-28T00:00:00Z", updated_at: "2026-10-28T00:00:00Z",
  image_url: "/api/v1/realistic-review-ugc/typography/references/ref-1/image",
};

function fixture(capabilities: TypographyReferencePage["capabilities"] = { read: true, upload: true, manage: true }): TypographyReferencePage {
  return {
    items: [reference], total: 1, offset: 0, limit: 40,
    stats: { total: 1, approved: 0, needs_review: 1, analyzing: 1 },
    capabilities,
  };
}

let host: HTMLElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.mocked(listTypographyReferences).mockResolvedValue(fixture());
  vi.mocked(listTypographyDna).mockResolvedValue({ items: [] });
  vi.mocked(uploadTypographyReferences).mockResolvedValue({
    total: 1, created: 1, duplicates: 0, failed: 0,
    items: [{ index: 0, filename: "idea.png", status: "created", reference }],
  });
  vi.mocked(patchTypographyReference).mockResolvedValue(reference);
  vi.mocked(archiveTypographyReference).mockResolvedValue({ id: reference.id, editorial_status: "archived" });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  document.body.replaceChildren();
  vi.clearAllMocks();
});

async function renderPage(page = fixture()) {
  await act(async () => root.render(<TypographyIdeasPage active firstPage={page} onCountChange={() => undefined} />));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}
async function click(button: Element | null) {
  expect(button).not.toBeNull();
  await act(async () => (button as HTMLElement).click());
}
async function openReference() {
  await click(host.querySelector<HTMLElement>(".typography-reference-image"));
  return document.querySelector<HTMLElement>("[data-testid=typography-detail-drawer]");
}

describe("Typography Ideas Stage 1 navigation and gated library", () => {
  it("uses only real server statistics and existing approved references, no generated layouts", async () => {
    await renderPage();
    expect(host.querySelectorAll("[data-testid=typography-reference-card]")).toHaveLength(1);
    expect(host.textContent).toContain("Typography ideas");
    expect(host.textContent).toContain("Needs review");
    expect(host.textContent?.toLowerCase()).toContain("balanced");
    expect(host.textContent).not.toContain("Generate eligible");
    expect(host.textContent).not.toContain("Auto match");
    expect(host.querySelector(".typography-ideas-counters")?.textContent).toContain("Approved");
    expect(host.querySelector(".typography-reference-image img")?.getAttribute("src")).toBe(reference.image_url);
    expect(vi.mocked(listTypographyReferences)).toHaveBeenCalled();
  });

  it("keeps upload and review out of read-only Viewer roles", async () => {
    vi.mocked(listTypographyReferences).mockResolvedValue(fixture({ read: true, upload: false, manage: false }));
    await renderPage(fixture({ read: true, upload: false, manage: false }));
    expect(host.querySelector("[data-testid=typography-upload-open]")).toBeNull();
    const detail = await openReference();
    expect(detail).not.toBeNull();
    expect(detail?.textContent).toContain("Read-only reference");
    expect(detail?.textContent).not.toContain("Edit metadata");
    expect(detail?.textContent).not.toContain("Approve");
    expect(patchTypographyReference).not.toHaveBeenCalled();
    await click(detail?.querySelector<HTMLButtonElement>('button[aria-label="Close details"]') ?? null);
    expect(document.querySelector("[data-testid=typography-detail-drawer]")).toBeNull();
  });

  it("blocks rights-unverified approval and shows DNA empty without invoking analyzer", async () => {
    await renderPage();
    const detail = await openReference();
    const approve = [...(detail?.querySelectorAll("button") || [])].find(element => element.textContent === "Approve");
    expect(approve?.disabled).toBe(true);
    await click([...(detail?.querySelectorAll("button[role=tab]") || [])].find(element => element.textContent === "Style DNA") || null);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(detail?.textContent).toContain("No analysis yet");
    expect(listTypographyDna).toHaveBeenCalledWith(reference.id, expect.anything());
    expect(patchTypographyReference).not.toHaveBeenCalled();
  });

  it("validates upload file types and only commits after Upload button", async () => {
    await renderPage();
    await click(host.querySelector<HTMLButtonElement>("[data-testid=typography-upload-open]"));
    const modal = document.querySelector<HTMLElement>("[data-testid=typography-upload-dialog]");
    expect(modal).not.toBeNull();
    expect(modal?.textContent).toContain("No automatic approval");
    const fileField = modal?.querySelector<HTMLInputElement>('input[type=file]');
    expect(fileField).not.toBeNull();
    const supported = new File(["test image bytes"], "idea.png", { type: "image/png" });
    const unsupported = new File(["text"], "idea.txt", { type: "text/plain" });
    await act(async () => {
      Object.defineProperty(fileField!, "files", { configurable: true, value: [supported, unsupported] });
      fileField!.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(modal?.textContent).toContain("Only PNG, JPEG and WebP");
    expect(uploadTypographyReferences).not.toHaveBeenCalled();
    await click([...modal!.querySelectorAll("button")].find(btn => btn.textContent?.includes("Upload 1 images")) || null);
    expect(uploadTypographyReferences).toHaveBeenCalledWith(
      [supported], { rights: "unknown", privacy: "private", sourceAttribution: "" }, expect.anything(),
    );
    expect(modal?.textContent).toContain("created");
  });

  it("does not archive until the user confirms and the API acknowledges", async () => {
    await renderPage();
    const detail = await openReference();
    await click([...(detail?.querySelectorAll("button") || [])].find(btn => btn.textContent === "Archive") || null);
    expect(archiveTypographyReference).not.toHaveBeenCalled();
    const confirm = detail?.querySelector(".typography-archive-confirm");
    expect(confirm?.textContent).toContain("Stored image files");
    await click([...confirm!.querySelectorAll("button")].find(btn => btn.textContent === "Cancel") || null);
    expect(archiveTypographyReference).not.toHaveBeenCalled();
    await click([...(detail?.querySelectorAll("button") || [])].find(btn => btn.textContent === "Archive") || null);
    const nextConfirm = detail?.querySelector(".typography-archive-confirm");
    await click([...nextConfirm!.querySelectorAll("button")].find(btn => btn.textContent === "Archive reference") || null);
    expect(archiveTypographyReference).toHaveBeenCalledWith(reference.id);
    expect(document.querySelector("[data-testid=typography-detail-drawer]")).toBeNull();
  });

  it("filters by server-validated layout/font/overlap components", async () => {
    await renderPage();
    const styleSelect = host.querySelector<HTMLSelectElement>('select[aria-label="Typography style component"]')!;
    await act(async () => {
      styleSelect.value = "overlap";
      styleSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(listTypographyReferences).toHaveBeenLastCalledWith(
      expect.objectContaining({ styleRole: "overlap" }), expect.anything(),
    );
    await click([...host.querySelectorAll("button")].find(btn => btn.textContent === "Reset") || null);
    expect(styleSelect.value).toBe("");
  });

  it("allows metadata edits only on explicit Save, while preserving source text", async () => {
    await renderPage();
    const detail = await openReference();
    await click([...(detail?.querySelectorAll("button") || [])].find(btn => btn.textContent === "Edit metadata") || null);
    expect(patchTypographyReference).not.toHaveBeenCalled();
    const scope = [...(detail?.querySelectorAll("select") || [])]
      .find(select => select.parentElement?.textContent?.includes("Reference scope"))!;
    await act(async () => {
      scope.value = "skill";
      scope.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const input = [...(detail?.querySelectorAll("input") || [])].find(
      field => field.parentElement?.textContent?.includes("Skill name"),
    )!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "concept-short-v1");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click([...(detail?.querySelectorAll("button") || [])].find(btn => btn.textContent === "Save changes") || null);
    expect(patchTypographyReference).toHaveBeenCalledWith(reference.id, expect.objectContaining({
      scope: "skill", skill_name: "concept-short-v1", source_rights_status: "unknown",
      reference_roles: ["layout", "font_mood", "overlap"],
    }));
  });

  it("closes the upload/dialog with Escape and restores focus", async () => {
    await renderPage();
    const opener = host.querySelector<HTMLButtonElement>("[data-testid=typography-upload-open]")!;
    opener.focus();
    await click(opener);
    expect(document.querySelector("[data-testid=typography-upload-dialog]")).not.toBeNull();
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelector("[data-testid=typography-upload-dialog]")).toBeNull();
    expect(document.activeElement).toBe(opener);
    const detail = await openReference();
    await click([...(detail?.querySelectorAll("button") || [])].find(btn => btn.textContent === "Archive") || null);
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(detail?.querySelector(".typography-archive-confirm")).toBeNull();
    expect(document.querySelector("[data-testid=typography-detail-drawer]")).not.toBeNull();
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelector("[data-testid=typography-detail-drawer]")).toBeNull();
  });

  it("exposes a preview-only Style Lab under the V2 subnav", async () => {
    const navigate = vi.fn();
    await act(async () => root.render(<Stage1Subnav view="jobs" onView={navigate} ideasCount={12} />));
    expect(host.querySelectorAll(".typography-stage1-subnav button")).toHaveLength(3);
    expect(host.textContent).toContain("Style Lab Preview");
    await click([...host.querySelectorAll("button")].find(button => button.textContent?.includes("Typography Ideas")) || null);
    expect(navigate).toHaveBeenCalledWith("ideas");
    await click([...host.querySelectorAll("button")].find(button => button.textContent?.includes("Style Lab")) || null);
    expect(navigate).toHaveBeenCalledWith("style-lab");
  });
});
