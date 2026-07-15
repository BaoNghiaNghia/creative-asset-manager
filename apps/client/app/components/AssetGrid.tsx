import type { Asset } from "../types";

const icons = { folder: "📁", image: "▧", video: "▶", pdf: "PDF", document: "DOC", other: "◇" };

type Props = {
  items: Asset[];
  path: Asset[];
  selected: Set<string>;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (id: string) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
};

export function AssetGrid({ items, path, selected, onOpen, onToggle, onPrefetch, onCancelPrefetch }: Props) {
  return <div className="grid">
    {items.map(item => <article
      className={selected.has(item.id) ? "selected" : ""}
      key={item.id}
      onPointerEnter={() => item.kind === "folder" && onPrefetch(item.id)}
      onPointerLeave={onCancelPrefetch}
    >
      <button className="check" onClick={() => onToggle(item.id)}>{selected.has(item.id) ? "✓" : ""}</button>
      <button className={"preview " + item.kind} onDoubleClick={() => item.kind === "folder" && onOpen(item.id, path)}>
        <span>{icons[item.kind]}</span>
      </button>
      <div>
        <button className="name" onDoubleClick={() => item.kind === "folder" && onOpen(item.id, path)}>{item.name}</button>
        <small>{item.kind === "folder" ? "Folder" : item.modified_at ? new Date(item.modified_at).toLocaleDateString() : item.mime_type}</small>
      </div>
    </article>)}
  </div>;
}
