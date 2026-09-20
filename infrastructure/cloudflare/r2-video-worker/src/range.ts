export type ParsedByteRange =
  | { kind: "none" }
  | { kind: "valid"; start: number; end: number; length: number }
  | { kind: "invalid" }
  | { kind: "unsatisfiable" };

const DECIMAL = "[0-9]{1,15}";
const SINGLE_RANGE = new RegExp("^bytes=(" + DECIMAL + ")?-(" + DECIMAL + ")?$");

/** Parse the only byte-range form supported by Phase 3B. */
export function parseSingleByteRange(value: string | null, size: number): ParsedByteRange {
  if (value === null) return { kind: "none" };
  if (!Number.isSafeInteger(size) || size < 0) return { kind: "invalid" };
  const match = SINGLE_RANGE.exec(value);
  if (!match || (match[1] === undefined && match[2] === undefined)) return { kind: "invalid" };
  if (size === 0) return { kind: "unsatisfiable" };
  const [, rawStart, rawEnd] = match;
  if (rawStart === undefined) {
    const suffixLength = Number(rawEnd);
    if (!Number.isSafeInteger(suffixLength) || suffixLength <= 0) return { kind: "invalid" };
    const length = Math.min(suffixLength, size);
    return { kind: "valid", start: size - length, end: size - 1, length };
  }
  const start = Number(rawStart);
  if (!Number.isSafeInteger(start) || start >= size) return { kind: "unsatisfiable" };
  if (rawEnd === undefined) return { kind: "valid", start, end: size - 1, length: size - start };
  const requestedEnd = Number(rawEnd);
  if (!Number.isSafeInteger(requestedEnd) || requestedEnd < start) return { kind: "invalid" };
  const end = Math.min(requestedEnd, size - 1);
  return { kind: "valid", start, end, length: end - start + 1 };
}
