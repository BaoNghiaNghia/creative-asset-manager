// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { isKeywordArtworkSkill, KeywordImageRowControls } from "./KeywordImageStage";
import type { KeywordImageRow, Stage2Skill } from "./types";

const keyword = {
  source: "local", skill_id: null, skill_name: "gatorhats-keyword-embroidery",
  display_name: "GatorHats Keyword Embroidery", description: "",
  default_version: null, latest_version: null, local_version: null,
  synced_version: null, ready: true, keyword_artwork_ready: true,
  sync_state: "ready", version_options: [],
} satisfies Stage2Skill;

describe("Stage 1 keyword skill selection", () => {
  it("accepts any ready image-generation Skill, including an existing redesign Skill", () => {
    expect(isKeywordArtworkSkill(keyword)).toBe(true);
    expect(isKeywordArtworkSkill({ ...keyword, skill_name: "hanh-redesign-8869-ver-3", keyword_artwork_ready: true })).toBe(true);
    expect(isKeywordArtworkSkill({ ...keyword, ready: false })).toBe(false);
  });
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const outputRow: KeywordImageRow = {
  keyword_id: "keyword-1", keyword: "BEACH PLEASE", search_volume: 150,
  source_image_url: null, status: "completed", job_id: "job-1",
  skill_name: "redesign-8869-v3", skill_version: null, retry_count: 0,
  attempt_count: 1, max_attempts: 3, error_code: null, error_message: null,
  output_url: "https://cdn.example.test/output.png", updated_at: null,
};

describe("Stage 1 compact output and action controls", () => {
  it("keeps output preview, regenerate, version history and logs in one compact toolbar", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const run = vi.fn(), versions = vi.fn(), logs = vi.fn();
    await act(async () => root.render(<KeywordImageRowControls
      row={outputRow} busy={false} canGenerate
      onRun={run} onVersions={versions} onLogs={logs} />));
    expect(host.querySelectorAll(".rrugc-keyword-result-cell")).toHaveLength(1);
    expect(host.querySelector(".rrugc-keyword-result-preview img")).not.toBeNull();
    expect(host.querySelector(".rrugc-keyword-result-caption")?.textContent).toBe("Latest output");
    expect(host.querySelector(".rrugc-keyword-result-actions")?.textContent).toContain("Versions");
    expect(host.querySelectorAll(".rrugc-keyword-result-action")).toHaveLength(3);
    const button = (label: string) => host.querySelector<HTMLButtonElement>('button[aria-label="' + label + '"]')!;
    expect(host.querySelector<HTMLAnchorElement>('a[aria-label="View generated output for BEACH PLEASE"]')?.href).toBe("https://cdn.example.test/output.png");
    expect(button("Regenerate BEACH PLEASE").title).toBe("Regenerate image");
    expect(button("View all generated images for BEACH PLEASE").title).toBe("View all generated images and versions");
    expect(button("View logs for BEACH PLEASE").title).toBe("View job logs");
    await act(async () => {
      button("Regenerate BEACH PLEASE").click();
      button("View all generated images for BEACH PLEASE").click();
      button("View logs for BEACH PLEASE").click();
    });
    expect(run).toHaveBeenCalledOnce();
    expect(versions).toHaveBeenCalledOnce();
    expect(logs).toHaveBeenCalledOnce();
    await act(async () => root.unmount());
    host.remove();
  });

  it("preserves Generate, Retry and Logs across job states with disabled guards", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const run = vi.fn();
    const render = (row: KeywordImageRow, canGenerate: boolean, busy = false) => root.render(
      <KeywordImageRowControls row={row} busy={busy} canGenerate={canGenerate}
        onRun={run} onVersions={() => undefined} onLogs={() => undefined} />);
    await act(async () => render({ ...outputRow, status: "not_run", job_id: null, output_url: null }, false));
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Generate BEACH PLEASE"]')?.disabled).toBe(true);
    await act(async () => render({ ...outputRow, status: "not_run", job_id: null, output_url: null }, true));
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Generate BEACH PLEASE"]')?.click());
    expect(run).toHaveBeenCalledOnce();
    await act(async () => render({ ...outputRow, status: "failed", output_url: null, retry_count: 3 }, true));
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Retry BEACH PLEASE"]')?.disabled).toBe(true);
    expect(host.querySelectorAll(".rrugc-keyword-result-action")).toHaveLength(2);
    await act(async () => render({ ...outputRow, status: "queued", output_url: null }, true, true));
    expect(host.querySelectorAll(".rrugc-keyword-result-action")).toHaveLength(1);
    expect(host.querySelector(".rrugc-keyword-result-pending")?.textContent).toContain("In queue");
    await act(async () => root.unmount());
    host.remove();
  });
});
