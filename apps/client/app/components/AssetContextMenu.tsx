import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { Asset } from "../types";

export type AssetContextMenuPosition = {
  x: number;
  y: number;
};

type Props = {
  item: Asset;
  position: AssetContextMenuPosition;
  onOpen: () => void;
  onDownload: () => void;
  onCopy: () => void;
  onMove: () => void;
  onDetails: () => void;
  onDelete: () => void;
  onClose: () => void;
};

const VIEWPORT_GAP = 8;

type IconName = "open" | "preview" | "download" | "copy" | "move" | "info" | "trash";

function MenuIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    open: <><path d="M3.5 6.5h6l2 2h9v10h-17z" /><path d="m14.5 5 3-3 3 3M17.5 2v9" /></>,
    preview: <><path d="M4 5h16v14H4z" /><path d="m7 16 4-4 3 3 2-2 4 4M8.5 9.5h.01" /></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4" /><path d="M4 19h16" /></>,
    copy: <><rect x="8" y="8" width="11" height="11" rx="1" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>,
    move: <><path d="M3 7h7l2 2h9v10H3z" /><path d="m13 13 2-2 2 2m-2-2v6" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
    trash: <><path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{paths[name]}</svg>;
}

export function clampContextMenuPosition(
  position: AssetContextMenuPosition,
  menuWidth: number,
  menuHeight: number,
  viewportWidth: number,
  viewportHeight: number,
): AssetContextMenuPosition {
  return {
    x: Math.max(VIEWPORT_GAP, Math.min(position.x, viewportWidth - menuWidth - VIEWPORT_GAP)),
    y: Math.max(VIEWPORT_GAP, Math.min(position.y, viewportHeight - menuHeight - VIEWPORT_GAP)),
  };
}

export function AssetContextMenu({
  item,
  position,
  onOpen,
  onDownload,
  onCopy,
  onMove,
  onDetails,
  onDelete,
  onClose,
}: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState(position);

  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    setPlacement(clampContextMenuPosition(
      position,
      menu.offsetWidth,
      menu.offsetHeight,
      window.innerWidth,
      window.innerHeight,
    ));
  }, [position]);

  useEffect(() => {
    function closeOnPointer(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) onClose();
    }
    function closeOnKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("pointerdown", closeOnPointer);
    window.addEventListener("keydown", closeOnKey);
    window.addEventListener("blur", onClose);
    return () => {
      window.removeEventListener("pointerdown", closeOnPointer);
      window.removeEventListener("keydown", closeOnKey);
      window.removeEventListener("blur", onClose);
    };
  }, [onClose]);

  function run(action: () => void) {
    action();
    onClose();
  }

  const style = {
    "--context-menu-left": placement.x + "px",
    "--context-menu-top": placement.y + "px",
  } as CSSProperties;

  return <div
    ref={menuRef}
    className="asset-context-menu"
    style={style}
    role="menu"
    aria-label={"Actions for " + item.name}
    onContextMenu={event => event.preventDefault()}
  >
    <button type="button" role="menuitem" autoFocus onClick={() => run(onOpen)}>
      <MenuIcon name={item.kind === "folder" ? "open" : "preview"} />
      <b>{item.kind === "folder" ? "Open folder" : "Open preview"}</b>
      <kbd>Enter</kbd>
    </button>
    {item.kind !== "folder" && <button type="button" role="menuitem" onClick={() => run(onDownload)}>
      <MenuIcon name="download" /><b>Download</b>
    </button>}
    <div className="asset-context-separator" role="separator" />
    <button type="button" role="menuitem" onClick={() => run(onCopy)}>
      <MenuIcon name="copy" /><b>Make a copy</b><kbd>Ctrl+C</kbd>
    </button>
    <button type="button" role="menuitem" onClick={() => run(onMove)}>
      <MenuIcon name="move" /><b>Move to...</b>
    </button>
    <button type="button" role="menuitem" onClick={() => run(onDetails)}>
      <MenuIcon name="info" /><b>File information</b>
    </button>
    <div className="asset-context-separator" role="separator" />
    <button type="button" role="menuitem" className="danger" onClick={() => run(onDelete)}>
      <MenuIcon name="trash" /><b>Move to trash</b><kbd>Delete</kbd>
    </button>
  </div>;
}
