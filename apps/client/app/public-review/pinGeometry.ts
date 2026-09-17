export type Rect = { left: number; top: number; width: number; height: number };
export type Point = { x: number; y: number };
export function renderedImageRect(box: Rect, naturalWidth: number, naturalHeight: number): Rect | null {
  if (!(box.width > 0 && box.height > 0 && naturalWidth > 0 && naturalHeight > 0)) return null;
  const scale = Math.min(box.width / naturalWidth, box.height / naturalHeight);
  const width = naturalWidth * scale, height = naturalHeight * scale;
  return { left: box.left + (box.width - width) / 2, top: box.top + (box.height - height) / 2, width, height };
}
export function normalizedPoint(box: Rect, naturalWidth: number, naturalHeight: number, clickX: number, clickY: number): Point | null {
  const content = renderedImageRect(box, naturalWidth, naturalHeight); if (!content) return null;
  const x = (clickX - content.left) / content.width, y = (clickY - content.top) / content.height;
  return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
}
export function pinPosition(box: Rect, naturalWidth: number, naturalHeight: number, x: number, y: number): Point | null {
  if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1)) return null;
  const content = renderedImageRect(box, naturalWidth, naturalHeight); return content ? { x: content.left + x * content.width, y: content.top + y * content.height } : null;
}
