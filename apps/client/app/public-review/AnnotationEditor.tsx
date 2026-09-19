import { useState } from "react";
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

  if (!editor) return null;

  const submit = async () => {
    if (!hasText || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(editor.getJSON() as EditorJson);
      editor.commands.clearContent();
      setHasText(false);
    } catch {
      // Preserve the draft for retry; the request layer retains its existing error handling.
    } finally {
      setSubmitting(false);
    }
  };

  return <div className="public-editor">
    <EditorContent editor={editor}/>
    <button type="button" className="public-editor-submit" disabled={!hasText || submitting} onClick={() => void submit()}>{submitLabel}</button>
  </div>;
}
