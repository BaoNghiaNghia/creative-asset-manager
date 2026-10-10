// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { formatKeywordJobDuration, isKeywordArtworkSkill, KeywordImageRowControls } from "./KeywordImageStage";
import type { KeywordImageRow, Stage2Skill } from "./types";

const keyword = {
  source: "local", skill_id: null, skill_name: "gatorhats-keyword-embroidery",
  display_name: "GatorHats Keyword Embroidery", description: "",
  default_version: null, latest_version: null, local_version: null,
  synced_version: null, ready: true, keyword_artwork_ready: true,
  sync_state: "ready", version_options: [],
} satisfies Stage2Skill;

describe("Stage 1 job elapsed time", () => {
  it("formats completed time precisely and does not invent missing timestamps", () => {
    expect(formatKeywordJobDuration("2026-10-10T01:00:00Z", "2026-10-10T01:01:23Z")).toBe("1m 23s");
    expect(formatKeywordJobDuration("2026-10-10T01:00:00Z", "2026-10-10T03:10:00Z")).toBe("2h 10m");
    expect(formatKeywordJobDuration("2026-10-10T01:00:00Z", null, Date.parse("2026-10-10T01:00:07Z"))).toBe("7s");
    expect(formatKeywordJobDuration(null, null)).toBe("—");
    expect(formatKeywordJobDuration("invalid", null)).toBe("—");
  });
});

describe("Stage 1 keyword skill selection", () => {
  it("accepts only ready Stage 1 six-design workflows advertised by the backend", () => {
    expect(isKeywordArtworkSkill({ ...keyword, skill_name: "gatorhats-stage1-six-designs" })).toBe(true);
    expect(isKeywordArtworkSkill({ ...keyword, skill_name: "hanh-redesign-8869-ver-4", keyword_artwork_ready: false })).toBe(false);
    expect(isKeywordArtworkSkill({ ...keyword, ready: false })).toBe(false);
  });
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const outputRow: KeywordImageRow = {
  keyword_id: "keyword-1", keyword: "BEACH PLEASE", search_volume: 150,
  source_image_url: null, status: "completed", job_id: "job-1",
  skill_name: "redesign-8869-v3", skill_version: null, retry_count: 0,
  saved_output_count: 1, attempt_count: 1, max_attempts: 1, error_code: null, error_message: null,
  output_url: "https://cdn.example.test/output.png", updated_at: null,
};

describe("Stage 1 compact output and action controls", () => {
  it("shows regenerate and logs, and queues a new version only after Accept", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const run = vi.fn(), logs = vi.fn();
    await act(async () => root.render(<KeywordImageRowControls
      row={outputRow} busy={false} canGenerate
      onRun={run} onLogs={logs} />));
    expect(host.querySelectorAll(".rrugc-keyword-result-cell")).toHaveLength(1);
    expect(host.querySelector(".rrugc-keyword-result-preview img")).not.toBeNull();
    expect(host.querySelector(".rrugc-keyword-result-caption")?.textContent).toBe("1 image saved");
    expect(host.querySelectorAll(".rrugc-keyword-result-action")).toHaveLength(2);
    const button = (label: string) => host.querySelector<HTMLButtonElement>('button[aria-label="' + label + '"]')!;
    expect(host.querySelector<HTMLAnchorElement>('a[aria-label="View generated output for BEACH PLEASE"]')?.href).toBe("https://cdn.example.test/output.png");
    expect(button("Retry BEACH PLEASE").title).toContain("Replace existing images");
    expect(button("Retry BEACH PLEASE").classList.contains("is-retry")).toBe(true);
    expect(button("View all generated images for BEACH PLEASE")).toBeNull();
    expect(button("View logs for BEACH PLEASE").title).toBe("View job logs");

    await act(async () => button("Retry BEACH PLEASE").click());
    expect(run).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"][aria-modal="true"] h2')?.textContent).toBe("Replace Stage 1 images?");
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("BEACH PLEASE");
    await act(async () => document.querySelector<HTMLButtonElement>('.rrugc-keyword-regenerate-buttons button')?.click());
    expect(document.querySelector(".rrugc-keyword-regenerate-dialog")).toBeNull();
    expect(run).not.toHaveBeenCalled();

    await act(async () => button("Retry BEACH PLEASE").click());
    await act(async () => document.querySelector<HTMLButtonElement>(".rrugc-keyword-regenerate-buttons .is-accept")?.click());
    expect(run).toHaveBeenCalledOnce();
    expect(document.querySelector(".rrugc-keyword-regenerate-dialog")).toBeNull();
    await act(async () => button("View logs for BEACH PLEASE").click());
    expect(logs).toHaveBeenCalledOnce();
    await act(async () => root.unmount());
    host.remove();
  });

  it("closes the regenerate confirmation on Escape or outside click without running", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const run = vi.fn();
    await act(async () => root.render(<KeywordImageRowControls
      row={outputRow} busy={false} canGenerate onRun={run} onLogs={() => undefined} showPreview={false} />));
    const regenerate = host.querySelector<HTMLButtonElement>('button[aria-label="Retry BEACH PLEASE"]')!;
    await act(async () => regenerate.click());
    expect(document.activeElement?.textContent).toBe("Cancel");
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelector(".rrugc-keyword-regenerate-dialog")).toBeNull();
    await act(async () => regenerate.click());
    const backdrop = document.querySelector<HTMLElement>(".rrugc-keyword-regenerate-backdrop")!;
    await act(async () => backdrop.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(document.querySelector(".rrugc-keyword-regenerate-dialog")).toBeNull();
    expect(run).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    host.remove();
  });

  it("allows manually confirmed replacement on failed jobs, including partial Drive uploads", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const run = vi.fn();
    const render = (row: KeywordImageRow, canGenerate: boolean, busy = false) => root.render(
      <KeywordImageRowControls row={row} busy={busy} canGenerate={canGenerate}
        onRun={run} onLogs={() => undefined} />);
    await act(async () => render({ ...outputRow, status: "not_run", job_id: null, output_url: null }, false));
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Redesign Qoutes BEACH PLEASE"]')?.disabled).toBe(true);
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Redesign Qoutes BEACH PLEASE"]')?.textContent).toBe("Redesign Qoutes");
    expect(host.querySelector(".rrugc-keyword-result-action.is-generate svg")).not.toBeNull();
    await act(async () => render({ ...outputRow, status: "not_run", job_id: null, output_url: null }, true));
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Redesign Qoutes BEACH PLEASE"]')?.click());
    expect(run).toHaveBeenCalledOnce();
    await act(async () => render({ ...outputRow, status: "failed", output_url: null, saved_output_count: 0, retry_count: 0 }, true));
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Retry BEACH PLEASE"]')).not.toBeNull();
    expect(host.querySelectorAll(".rrugc-keyword-result-action")).toHaveLength(2);
    await act(async () => render({ ...outputRow, status: "failed", output_url: null, saved_output_count: 2 }, true));
    expect(host.querySelector(".rrugc-keyword-result-caption")?.textContent).toContain("partial");
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="View all generated images for BEACH PLEASE"]')).toBeNull();
    await act(async () => render({ ...outputRow, status: "failed", output_url: null, saved_output_count: 2, upload_recovery_available: true }, true));
    const retry = host.querySelector<HTMLButtonElement>('button[aria-label="Retry BEACH PLEASE"]');
    expect(retry?.title).toContain("Replace existing images");
    await act(async () => retry?.click());
    expect(run).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("permanently deleted");
    await act(async () => document.querySelector<HTMLButtonElement>(".rrugc-keyword-regenerate-buttons .is-accept")?.click());
    expect(run).toHaveBeenCalledTimes(2);
    await act(async () => render({ ...outputRow, status: "queued", output_url: null, saved_output_count: 0 }, true, true));
    expect(host.querySelectorAll(".rrugc-keyword-result-action")).toHaveLength(1);
    expect(host.querySelector(".rrugc-keyword-result-pending")?.textContent).toContain("In queue");
    await act(async () => root.unmount());
    host.remove();
  });
});

describe("Stage 1 zero-output recovery", () => {
  it("offers an explicit new generation confirmation for BIG BROTHER, never an automatic retry", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const run = vi.fn();
    const failed: KeywordImageRow = {
      ...outputRow, keyword: "BIG BROTHER", status: "failed",
      job_id: "failed-big-brother", output_url: null, saved_output_count: 0,
      error_code: "stage1_six_outputs_invalid",
      error_message: "Stage 1 returned 0 PNG candidates, 0/6 uniquely numbered finals",
    };
    await act(async () => root.render(
      <KeywordImageRowControls row={failed} busy={false} canGenerate onRun={run} onLogs={() => undefined} />,
    ));
    const action = host.querySelector<HTMLButtonElement>('button[aria-label="Retry BIG BROTHER"]');
    expect(action).not.toBeNull();
    await act(async () => action?.click());
    expect(run).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain("permanently deleted");
    await act(async () => document.querySelector<HTMLButtonElement>(".rrugc-keyword-regenerate-buttons .is-accept")?.click());
    expect(run).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
    host.remove();
  });

  it("allows one confirmed manual run for new missing-tool or failed-turn error categories", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    for (const code of [
      "stage1_imagegen_unavailable", "stage1_imagegen_limited",
      "stage1_codex_turn_failed", "stage1_imagegen_not_invoked",
      "stage1_imagegen_no_output", "stage1_no_generated_images",
    ]) {
      const row: KeywordImageRow = {
        ...outputRow, status: "failed", saved_output_count: 0,
        output_url: null, error_code: code,
      };
      await act(async () => root.render(<KeywordImageRowControls
        row={row} busy={false} canGenerate onRun={() => undefined} onLogs={() => undefined}
      />));
      expect(host.querySelector('button[aria-label="Retry BEACH PLEASE"]')).not.toBeNull();
    }
    await act(async () => root.unmount());
    host.remove();
  });

  it("does not allow a new paid generation after a partial-image or Google Drive failure", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    for (const failure of [
      { code: "stage1_six_outputs_invalid", message: "5 PNG candidates", count: 0 },
      { code: "managed_storage_temporarily_unavailable", message: "Drive HTTP 500", count: 0 },
      { code: "stage1_six_outputs_invalid", message: "0 PNG candidates", count: 2 },
    ]) {
      await act(async () => root.render(<KeywordImageRowControls
        row={{ ...outputRow, status: "failed", output_url: null, saved_output_count: failure.count,
          error_code: failure.code, error_message: failure.message }}
        busy={false} canGenerate onRun={() => undefined} onLogs={() => undefined} />));
      expect(host.querySelector('button[aria-label="Generate again BEACH PLEASE"]')).toBeNull();
    }
    await act(async () => root.unmount());
    host.remove();
  });
});