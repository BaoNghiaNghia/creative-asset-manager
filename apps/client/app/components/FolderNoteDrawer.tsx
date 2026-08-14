import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export type FolderNote = { requested_folder_id: string; note_owner_folder_id?: string | null; note_owner_folder_name?: string | null; is_inherited?: boolean; content_markdown: string; updated_at?: string | null; updated_by?: string | null };

type Props = { folderId: string; folderName: string; provider: string; externalSourceId?: string | null; canManage: boolean; onClose: () => void; onSaved: (note: FolderNote) => void; };

function noteUrl(folderId: string, provider: string, externalSourceId?: string | null) {
  const params = new URLSearchParams({ provider });
  if (externalSourceId) params.set("external_source_id", externalSourceId);
  return "/api/explorer/folders/" + encodeURIComponent(folderId) + "/note?" + params.toString();
}

export function FolderNoteDrawer({ folderId, folderName, provider, externalSourceId, canManage, onClose, onSaved }: Props) {
  const [note, setNote] = useState<FolderNote | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setNote(null); setDraft(""); setError(""); setEditing(false);
    fetch(noteUrl(folderId, provider, externalSourceId), { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(response.status === 403 ? "You do not have access to this note." : "Unable to load note.");
        return response.json() as Promise<FolderNote>;
      })
      .then(value => { setNote(value); setDraft(value.content_markdown || ""); })
      .catch(value => { if (value.name !== "AbortError") setError(value.message); });
    return () => controller.abort();
  }, [folderId, provider, externalSourceId]);
  useEffect(() => () => { if (saveTimer.current) window.clearTimeout(saveTimer.current); }, []);
  useEffect(() => { if (editing) textareaRef.current?.focus(); }, [editing]);
  async function save(content = draft) {
    if (!canManage || saving) return;
    setSaving(true); setError("");
    try {
      const response = await fetch(noteUrl(folderId, provider, externalSourceId), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content_markdown: content }) });
      if (!response.ok) throw new Error(response.status === 403 ? "You do not have permission to edit this note." : "Unable to save note.");
      const value = await response.json() as FolderNote;
      setNote(value); setDraft(value.content_markdown || ""); setEditing(false); onSaved(value);
    } catch (value) { setError(value instanceof Error ? value.message : "Unable to save note."); }
    finally { setSaving(false); }
  }
  function scheduleSave(value: string) {
    setDraft(value);
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => { void save(value); }, 1000);
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (editing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void save(); }
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  });
  return <div className="folder-note-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="folder-note-drawer" role="dialog" aria-modal="true" aria-label={"Folder note for " + folderName}>
      <header><div><small>PRODUCT NOTE</small><strong>{folderName}</strong>{note?.is_inherited && <em>Note for {note.note_owner_folder_name}</em>}</div><button type="button" onClick={onClose} aria-label="Close note">x</button></header>
      {error ? <p className="folder-note-error" role="alert">{error}</p> : note === null ? <p className="folder-note-loading">Loading note...</p> : editing ? <textarea ref={textareaRef} value={draft} maxLength={50000} onChange={event => scheduleSave(event.target.value)} placeholder="Write a Markdown note..." aria-label="Folder Markdown note" /> : <div className="folder-note-rendered">{draft ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown> : <p>No note yet.</p>}</div>}
      <footer>{canManage && <button type="button" className="primary" disabled={saving || note === null} onClick={() => editing ? void save() : setEditing(true)}>{editing ? (saving ? "Saving..." : "Save note") : (draft ? "Edit note" : "+ Add note")}</button>}</footer>
    </aside>
  </div>;
}
