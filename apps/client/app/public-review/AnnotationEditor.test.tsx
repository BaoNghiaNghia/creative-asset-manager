// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { AnnotationEditor } from "./AnnotationEditor";
import type { EditorJson } from "./RichAnnotation";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const savedV2Document: EditorJson = {
  type: "doc",
  content: [
    {
      type: "paragraph",
      content: [
        { type: "text", text: "Old note ", marks: [{ type: "bold" }] },
        { type: "text", text: "with link", marks: [{ type: "link", attrs: { href: "https://example.com/comment" } }] },
      ],
    },
    { type: "taskList", content: [
      { type: "taskItem", attrs: { checked: true }, content: [{ type: "paragraph", content: [{ type: "text", text: "Reviewed" }] }] },
    ] },
  ],
};

async function mount(initial?: EditorJson, onSubmit = vi.fn(async (_content: EditorJson) => undefined)) {
  const host = document.createElement("div");
  document.body.append(host);
  const root: Root = createRoot(host);
  await act(async () => {
    root.render(<AnnotationEditor initial={initial} onSubmit={onSubmit}/>);
    await Promise.resolve();
  });
  return { host, root, onSubmit };
}
async function click(button: HTMLButtonElement) {
  await act(async () => {
    button.click();
    await Promise.resolve();
  });
}

// jsdom does not implement Range geometry, which ProseMirror needs when
// focusing the emoji insertion caret. Real browsers provide these methods.
const originalRangeRects = Object.getOwnPropertyDescriptor(Range.prototype, "getClientRects");
const originalRangeBounds = Object.getOwnPropertyDescriptor(Range.prototype, "getBoundingClientRect");
beforeAll(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true, value: () => document.createElement("span").getClientRects(),
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true, value: () => document.createElement("span").getBoundingClientRect(),
  });
});
afterAll(() => {
  if (originalRangeRects) Object.defineProperty(Range.prototype, "getClientRects", originalRangeRects);
  else Reflect.deleteProperty(Range.prototype, "getClientRects");
  if (originalRangeBounds) Object.defineProperty(Range.prototype, "getBoundingClientRect", originalRangeBounds);
  else Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
});
afterEach(() => {
  document.body.replaceChildren();
});

describe("AnnotationEditor TipTap 3 migration", () => {
  it("keeps emoji, input, and submit in the same layout order", async () => {
    const { host, root } = await mount();
    const editor = host.querySelector(".public-editor");
    expect(editor).not.toBeNull();
    expect(Array.from(editor!.children).map(node => node.className)).toEqual([
      "public-editor-emoji",
      "public-editor-input",
      "public-editor-submit",
    ]);
    await act(async () => root.unmount());
  });

  it("opens legacy TipTap 2 JSON and submits the same marks and task checklist", async () => {
    const { host, root, onSubmit } = await mount(savedV2Document);
    const editable = host.querySelector<HTMLElement>(".public-tiptap");
    expect(editable?.textContent).toContain("Old note");
    expect(editable?.textContent).toContain("Reviewed");
    expect(editable?.querySelector("strong")?.textContent).toBe("Old note ");
    expect(editable?.querySelector<HTMLAnchorElement>("a")?.getAttribute("href")).toBe("https://example.com/comment");
    expect(editable?.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(true);

    const submit = host.querySelector<HTMLButtonElement>(".public-editor-submit")!;
    expect(submit.disabled).toBe(false);
    await click(submit);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const document = onSubmit.mock.calls[0]![0];
    expect(document.content?.[0]?.content?.[0]?.marks?.[0]?.type).toBe("bold");
    expect(document.content?.[0]?.content?.[1]?.marks?.[0]?.attrs?.href).toBe("https://example.com/comment");
    expect(document.content?.[1]?.type).toBe("taskList");
    expect(document.content?.[1]?.content?.[0]?.attrs?.checked).toBe(true);
    expect(submit.disabled).toBe(true);
    await act(async () => root.unmount());
  });

  it("does not turn unsafe stored link protocols into clickable links", async () => {
    const malicious: EditorJson = { type: "doc", content: [{ type: "paragraph", content: [
      { type: "text", text: "Do not click", marks: [{ type: "link", attrs: { href: "javascript:alert(1)" } }] },
    ] }] };
    const { host, root } = await mount(malicious);
    const anchor = host.querySelector<HTMLAnchorElement>(".public-tiptap a");
    expect(anchor?.getAttribute("href") || "").not.toMatch(/^javascript:/i);
    expect(host.querySelector(".public-tiptap")?.textContent).toContain("Do not click");
    await act(async () => root.unmount());
  });

  it("does not merge hostile own __proto__ link attributes into the DOM", async () => {
    const attrs = JSON.parse('{"href":"https://example.com","__proto__":{"onclick":"alert(1)"}}') as Record<string, unknown>;
    const document: EditorJson = {
      type: "doc",
      content: [{ type: "paragraph", content: [
        { type: "text", text: "Safe label", marks: [{ type: "link", attrs }] },
      ] }],
    };
    const { host, root } = await mount(document);
    const anchor = host.querySelector<HTMLAnchorElement>(".public-tiptap a");
    expect(anchor?.getAttribute("href")).toBe("https://example.com");
    expect(anchor?.hasAttribute("onclick")).toBe(false);
    expect(({} as { onclick?: string }).onclick).toBeUndefined();
    await act(async () => root.unmount());
  });

  it("inserts an emoji and emits valid document JSON", async () => {
    const { host, root, onSubmit } = await mount();
    const submit = host.querySelector<HTMLButtonElement>(".public-editor-submit")!;
    expect(submit.disabled).toBe(true);
    await click(host.querySelector<HTMLButtonElement>(".public-editor-emoji-toggle")!);
    await click(host.querySelector<HTMLButtonElement>('[aria-label="Insert 👍"]')!);
    expect(host.querySelector(".public-tiptap")?.textContent).toContain("👍");
    expect(submit.disabled).toBe(false);
    await click(submit);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(onSubmit.mock.calls[0]![0])).toContain("👍");
    await act(async () => root.unmount());
  });

  it("preserves an existing draft when the save request fails", async () => {
    const onSubmit = vi.fn(async (_content: EditorJson) => { throw new Error("offline"); });
    const { host, root } = await mount(savedV2Document, onSubmit);
    await click(host.querySelector<HTMLButtonElement>(".public-editor-submit")!);
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(host.querySelector(".public-tiptap")?.textContent).toContain("Old note");
    expect(host.querySelector<HTMLButtonElement>(".public-editor-submit")?.disabled).toBe(false);
    await act(async () => root.unmount());
  });
});
