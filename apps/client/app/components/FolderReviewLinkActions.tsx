import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Asset } from "../types";

export function reviewShareIdForFolder(
  item: Asset | undefined,
  activeExternalSourceId: string | null | undefined,
  reviewLinkShareIds: ReadonlyMap<string, string>,
): string | null {
  if (!item || item.kind !== "folder") return null;
  const externalSourceId = item.external_source_id || activeExternalSourceId;
  if (!externalSourceId) return null;
  return reviewLinkShareIds.get(`${externalSourceId}:${item.id}`) || null;
}

export function FolderReviewLinkActions({
  item,
  shareId,
  onCopyReviewLink,
  onRefreshReviewLink,
  titleContext = false,
}: {
  item: Asset;
  shareId: string;
  onCopyReviewLink: (shareId: string, item: Asset) => void | Promise<void>;
  onRefreshReviewLink: (shareId: string, item: Asset) => void | Promise<void>;
  titleContext?: boolean;
}) {
  const [menu, setMenu] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    if (!menu) return;
    const closeOnOutsidePointer = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(".folder-share-menu, .folder-share-trigger")) return;
      setMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    const closeOnViewportChange = () => setMenu(null);
    document.addEventListener("mousedown", closeOnOutsidePointer);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsidePointer);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [menu]);

  return (
    <>
      <button
        type="button"
        className={
          "folder-share-trigger" +
          (titleContext ? " folder-title-share-trigger" : "")
        }
        aria-label={"Shared link actions for " + item.name}
        title="Shared link actions"
        aria-haspopup="menu"
        aria-expanded={Boolean(menu)}
        onDoubleClick={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.stopPropagation();
          if (menu) {
            setMenu(null);
            return;
          }
          const rect = event.currentTarget.getBoundingClientRect();
          const menuWidth = 260;
          const menuHeight = 90;
          const left = Math.max(
            8,
            Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8),
          );
          const below = rect.bottom + 6;
          const top =
            below + menuHeight <= window.innerHeight - 8
              ? below
              : Math.max(8, rect.top - menuHeight - 6);
          setMenu({ left, top });
        }}
      >
        <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
          <circle cx="4" cy="10" r="1.5" />
          <circle cx="10" cy="10" r="1.5" />
          <circle cx="16" cy="10" r="1.5" />
        </svg>
      </button>
      {menu &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            className="folder-share-menu"
            role="menu"
            aria-label={"Shared link actions for " + item.name}
            style={{ left: menu.left, top: menu.top }}
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenu(null);
                void onCopyReviewLink(shareId, item);
              }}
            >
              Sao chép đường dẫn chia sẻ
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenu(null);
                void onRefreshReviewLink(shareId, item);
              }}
            >
              Cập nhật đường dẫn chia sẻ
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
