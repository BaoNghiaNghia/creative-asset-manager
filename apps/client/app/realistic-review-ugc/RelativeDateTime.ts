/** Shared Stage 0/Stage 1 date display: identical to Stage 1 Finished at. */
export function formatRelativeDateTime(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "—";
  const elapsed = now - timestamp;
  if (elapsed < 0 || elapsed >= 24 * 60 * 60 * 1000) {
    return new Date(timestamp).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  }
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes === 0) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  return `${Math.floor(minutes / 60)}h trước`;
}
