import type { Asset } from "../types";

type EmptyAssetsProps = {
  query: string;
  path: Asset[];
  onClearSearch: () => void;
  onOpen: (id: string, ancestors?: Asset[]) => void;
};

function EmptyIllustration({ search }: { search: boolean }) {
  if (search) {
    return <svg viewBox="0 0 48 48" aria-hidden="true">
      <path d="M12 8.5h17l7 7V27" />
      <path d="M29 8.5V16h7" />
      <path d="M12 8.5v27h14" />
      <circle cx="31.5" cy="32" r="7" />
      <path d="m36.5 37 4 4" />
    </svg>;
  }

  return <svg viewBox="0 0 48 48" aria-hidden="true">
    <path d="M5.5 14.5h15l4-5h9l4 5h5v25h-37z" />
    <path d="M5.5 19.5h37" />
    <path d="M17.5 30h13" />
  </svg>;
}

export function EmptyAssets({ query, path, onClearSearch, onOpen }: EmptyAssetsProps) {
  const searchQuery = query.trim();
  const isSearch = Boolean(searchQuery);
  const parent = path.at(-2);

  const openParent = () => {
    if (!parent) return;
    onOpen(parent.id, path.slice(0, -2));
  };

  return <section className="assets-empty" aria-live="polite">
    <span className={"assets-empty-icon " + (isSearch ? "search" : "folder")}>
      <EmptyIllustration search={isSearch} />
    </span>

    <h2>{isSearch ? `No results for “${searchQuery}”` : "This folder is empty"}</h2>
    <p>
      {isSearch
        ? "Try a shorter name, check the spelling, or search with another keyword."
        : "There are no folders or files here yet. Choose another folder or add assets using Upload."}
    </p>

    <div className="assets-empty-actions">
      {isSearch && <button className="primary" type="button" onClick={onClearSearch}>
        Clear search
      </button>}
      {!isSearch && parent && <button className="secondary" type="button" onClick={openParent}>
        Back to {parent.name}
      </button>}
    </div>
  </section>;
}
