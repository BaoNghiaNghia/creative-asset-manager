// @ts-expect-error Vitest executes this test-only import in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import appSource from "./App.tsx?raw";

const globalStyles = readFileSync(new URL("../styles/global.css", import.meta.url), "utf8");

describe("upload progress panel", () => {
  it("keeps the header fixed and scrolls after six upload rows", () => {
    expect(appSource).toContain('className="upload-list" role="list"');
    expect(appSource).toContain('role="listitem"');
    expect(globalStyles).toContain(
      ".upload-list{max-height:min(246px,calc(100dvh - 130px));overflow-y:auto",
    );
    expect(globalStyles).toContain(
      ".upload-list .upload-row{min-height:41px;box-sizing:border-box}",
    );
  });

  it("keeps external file drops wired to the uploader after the New menu was removed", () => {
    expect(appSource).toContain("function handleFileDrop");
    expect(appSource).toContain("if (!internal) void explorer.uploadFiles(files);");
    expect(appSource).toContain(".catch(() => void explorer.uploadFiles(files));");
    expect(appSource).toContain("void explorer.uploadFiles(files);");
    expect(appSource).not.toContain("function chooseUploadFiles()");
  });

  it("shows real byte progress, ETA, completion and server errors", () => {
    expect(appSource).toContain('upload.error || "Upload failed"');
    expect(appSource).toContain("formatUploadEta(upload.etaSeconds)");
    expect(appSource).toContain("strokeDashoffset={100 - upload.progress}");
    expect(appSource).toContain('`Uploading · ${Math.round(upload.progress)}%');
    expect(globalStyles).toContain(
      ".upload-panel:not(.desktop-ingestion-panel) .upload-row{grid-template-columns:22px minmax(0,1fr) minmax(132px,220px) 24px}",
    );
    expect(globalStyles).toContain(".upload-progress-value{stroke:#2f63c9;stroke-linecap:round");
  });
});
