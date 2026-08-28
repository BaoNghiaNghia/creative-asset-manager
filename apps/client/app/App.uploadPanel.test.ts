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
});
