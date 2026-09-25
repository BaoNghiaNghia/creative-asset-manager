// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { AnnotationEditor } from "./AnnotationEditor";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  document.body.replaceChildren();
});

describe("AnnotationEditor layout", () => {
  it("orders emoji, comment input, then submit", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<AnnotationEditor onSubmit={() => undefined}/>);
      await Promise.resolve();
    });

    const editor = host.querySelector(".public-editor");
    expect(editor).not.toBeNull();
    expect(Array.from(editor!.children).map(node => node.className)).toEqual([
      "public-editor-emoji",
      "public-editor-input",
      "public-editor-submit",
    ]);

    await act(async () => root.unmount());
  });
});
