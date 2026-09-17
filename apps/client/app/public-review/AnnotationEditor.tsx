import { useEffect, useMemo, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import type { EditorJson } from "./RichAnnotation";

const commands = [
  ["Text", "paragraph"], ["Heading 1", "heading1"], ["Heading 2", "heading2"], ["Heading 3", "heading3"], ["Bulleted list", "bullet"], ["Numbered list", "ordered"], ["To-do", "task"], ["Quote", "quote"], ["Divider", "divider"],
] as const;
type Command = typeof commands[number][1];
const allowedUrl = (value: string) => { try { const url = new URL(value); return url.protocol === "http:" || url.protocol === "https:"; } catch { return false; } };

export function AnnotationEditor({ initial, placeholder = "Write a note…", submitLabel = "Add note", onSubmit, onCancel }: { initial?: EditorJson; placeholder?: string; submitLabel?: string; onSubmit: (document: EditorJson) => void; onCancel?: () => void }) {
  const [query, setQuery] = useState(""); const [menu, setMenu] = useState(false); const [active, setActive] = useState(0);
  const editor = useEditor({ extensions: [StarterKit.configure({ codeBlock: false, heading: { levels: [1, 2, 3] } }), TaskList, TaskItem.configure({ nested: true }), Link.configure({ openOnClick: false, validate: allowedUrl }), Placeholder.configure({ placeholder })], content: initial || { type: "doc", content: [{ type: "paragraph" }] }, editorProps: { attributes: { class: "public-tiptap", "aria-label": placeholder } }, onUpdate: ({ editor: current }) => { const text = current.getText(); const match = /(?:^|\n)\/([^\n]*)$/.exec(text); setMenu(Boolean(match)); setQuery(match?.[1] || ""); setActive(0); } });
  const visible = useMemo(() => commands.filter(([label]) => label.toLowerCase().includes(query.toLowerCase())), [query]);
  useEffect(() => { if (!editor) return; const handler = (event: KeyboardEvent) => { if (!menu) return; if (event.key === "Escape") { event.preventDefault(); setMenu(false); return; } if (event.key === "ArrowDown") { event.preventDefault(); setActive(x => Math.min(x + 1, Math.max(visible.length - 1, 0))); return; } if (event.key === "ArrowUp") { event.preventDefault(); setActive(x => Math.max(x - 1, 0)); return; } if (event.key === "Enter" && visible[active]) { event.preventDefault(); apply(visible[active][1]); } }; editor.view.dom.addEventListener("keydown", handler); return () => editor.view.dom.removeEventListener("keydown", handler); });
  if (!editor) return null;
  const apply = (command: Command) => { const range = editor.state.selection; const before = editor.state.doc.textBetween(Math.max(0, range.from - query.length - 1), range.from, "\n", "\n"); if (before.startsWith("/")) editor.chain().focus().deleteRange({ from: Math.max(1, range.from - query.length - 1), to: range.from }).run(); const chain = editor.chain().focus(); if (command === "paragraph") chain.setParagraph().run(); else if (command === "heading1") chain.toggleHeading({ level: 1 }).run(); else if (command === "heading2") chain.toggleHeading({ level: 2 }).run(); else if (command === "heading3") chain.toggleHeading({ level: 3 }).run(); else if (command === "bullet") chain.toggleBulletList().run(); else if (command === "ordered") chain.toggleOrderedList().run(); else if (command === "task") chain.toggleTaskList().run(); else if (command === "quote") chain.toggleBlockquote().run(); else chain.setHorizontalRule().run(); setMenu(false); };
  const link = () => { const value = window.prompt("Link URL"); if (!value || !allowedUrl(value)) return; editor.chain().focus().extendMarkRange("link").setLink({ href: value }).run(); };
  const button = (label: string, run: () => void, pressed = false) => <button type="button" aria-label={label} title={label} aria-pressed={pressed} onMouseDown={e => { e.preventDefault(); run(); }}>{label}</button>;
  return <div className="public-editor"> <div className="public-editor-toolbar">{button("Bold", () => editor.chain().focus().toggleBold().run(), editor.isActive("bold"))}{button("Italic", () => editor.chain().focus().toggleItalic().run(), editor.isActive("italic"))}{button("Strike", () => editor.chain().focus().toggleStrike().run(), editor.isActive("strike"))}{button("Inline code", () => editor.chain().focus().toggleCode().run(), editor.isActive("code"))}{button("Link", link, editor.isActive("link"))}</div><EditorContent editor={editor}/>{menu && <div className="public-slash-menu" role="listbox" aria-label="Editor commands">{visible.map(([label, command], index) => <button type="button" role="option" aria-selected={index === active} className={index === active ? "active" : ""} key={command} onMouseDown={e => { e.preventDefault(); apply(command); }}>{label}</button>)}</div>}<div className="public-editor-actions"><button type="button" onClick={() => onSubmit(editor.getJSON() as EditorJson)}>{submitLabel}</button>{onCancel && <button type="button" onClick={onCancel}>Cancel</button>}</div></div>;
}
