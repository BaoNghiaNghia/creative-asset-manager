/** Fail closed: a 2xx HTTP response is not proof of a successful disposable QA. */
export function requireVerifiedDisposableWriteback(result: unknown): void {
  const candidate = result && typeof result === "object"
    ? result as Record<string, unknown>
    : {};
  const required = [
    "verified", "disposable_copy_trashed", "source_file_unchanged_by_qa",
  ];
  const unverified = required.filter((field) => candidate[field] !== true);
  if (unverified.length) {
    throw new Error(`Google Sheets QA failed: ${unverified.join(", ")} not verified. No production writeback is authorized.`);
  }
}
