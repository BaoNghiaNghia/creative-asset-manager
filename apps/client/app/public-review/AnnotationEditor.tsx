import { useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import type { EditorJson } from "./RichAnnotation";

const allowedUrl = (value: string) => {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
};

const reviewEmojis = [
  "😀", "😄", "😂", "😊", "😍", "🥰", "😎", "🤩",
  "👍", "👎", "👏", "🙌", "🙏", "💪", "👌", "🤝",
  "❤️", "🧡", "💛", "💚", "💙", "💜", "🤍", "🖤",
  "🔥", "✨", "⭐", "💯", "✅", "❌", "👀", "💡",
  "🎉", "🎯", "🚀", "📌", "📝", "💬", "🤣", "😭",
];

export function AnnotationEditor({
  initial,
  placeholder = "Bình luận...",
  submitLabel = "Đăng",
  onSubmit,
}: {
  initial?: EditorJson;
  placeholder?: string;
  submitLabel?: string;
  onSubmit: (document: EditorJson) => void | Promise<void>;
}) {
  const [hasText, setHasText] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const emojiPanelRef = useRef<HTMLDivElement>(null);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ codeBlock: false }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Link.configure({ openOnClick: false, validate: allowedUrl }),
      Placeholder.configure({ placeholder }),
    ],
    content: initial || { type: "doc", content: [{ type: "paragraph" }] },
    editorProps: { attributes: { class: "public-tiptap", "aria-label": placeholder } },
    onCreate: ({ editor: current }) => setHasText(Boolean(current.getText().trim())),
    onUpdate: ({ editor: current }) => setHasText(Boolean(current.getText().trim())),
  });

  useEffect(() => {
    if (!emojiOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!emojiPanelRef.current?.contains(event.target as Node)) setEmojiOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setEmojiOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside, true);
    document.addEventListener("keydown", closeOnEscape, true);
    return () => {
      document.removeEventListener("pointerdown", closeOutside, true);
      document.removeEventListener("keydown", closeOnEscape, true);
    };
  }, [emojiOpen]);

  if (!editor) return null;

  const submit = async () => {
    if (!hasText || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(editor.getJSON() as EditorJson);
      editor.commands.clearContent();
      setHasText(false);
      setEmojiOpen(false);
    } catch {
      // Preserve the draft for retry; the request layer retains its existing error handling.
    } finally {
      setSubmitting(false);
    }
  };

  const insertEmoji = (emoji: string) => {
    editor.chain().focus().insertContent(emoji).run();
    setHasText(Boolean(editor.getText().trim()));
    setEmojiOpen(false);
  };

  return <div className="public-editor">
    <div className="public-editor-input"><EditorContent editor={editor}/></div>
    <div className="public-editor-emoji" ref={emojiPanelRef}>
      <button
        type="button"
        className="public-editor-emoji-toggle"
        aria-label="Emoji"
        title="Emoji"
        aria-expanded={emojiOpen}
        onClick={() => setEmojiOpen(value => !value)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="8.5"/>
          <circle cx="9" cy="10" r="1"/>
          <circle cx="15" cy="10" r="1"/>
          <path d="M8.5 14c1 1.5 2.1 2.2 3.5 2.2s2.5-.7 3.5-2.2"/>
        </svg>
      </button>
      {emojiOpen && <div className="public-emoji-picker" role="dialog" aria-label="Choose emoji">
        <div className="public-emoji-picker-title">Emoji</div>
        <div className="public-emoji-grid">
          {reviewEmojis.map(emoji => <button
            type="button"
            key={emoji}
            aria-label={"Insert " + emoji}
            title={emoji}
            onMouseDown={event => event.preventDefault()}
            onClick={() => insertEmoji(emoji)}
          >{emoji}</button>)}
        </div>
      </div>}
    </div>
    <button type="button" className="public-editor-submit" disabled={!hasText || submitting} onClick={() => void submit()}>{submitLabel}</button>
  </div>;
}
