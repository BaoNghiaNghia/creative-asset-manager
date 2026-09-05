import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readdir, realpath } from "node:fs/promises";
import path from "node:path";

export type IngestionItemStatus =
  | "scanning" | "hashing" | "duplicate" | "ready" | "uploading"
  | "paused" | "completed" | "failed" | "cancelled" | "unsupported" | "changed";

type InternalItem = {
  id: string;
  jobId: string;
  absolutePath: string;
  rootPath: string;
  filename: string;
  relativePath: string;
  mimeType: string;
  size: number;
  mtimeMs: number;
  sha256?: string;
  status: IngestionItemStatus;
  bytesUploaded: number;
  attempts: number;
  errorCode?: string;
  abort?: AbortController;
};

export type IngestionItemView = Omit<InternalItem, "absolutePath" | "rootPath" | "mtimeMs" | "abort" | "sha256">;
export type IngestionJobView = {
  id: string; status: "scanning" | "ready" | "paused" | "completed" | "cancelled" | "failed";
  rootCount: number; discovered: number; supported: number; duplicates: number;
  completed: number; failed: number; skipped: number; uploading: number;
  items: IngestionItemView[];
};
export type Destination = { parentId: string; provider: "google-drive"; externalSourceId?: string };
export type UploadTransport = {
  preflight(hashes: readonly string[]): Promise<ReadonlySet<string>>;
  upload(item: { absolutePath: string; filename: string; mimeType: string; size: number; destination: Destination }, signal: AbortSignal, progress: (bytes: number) => void): Promise<void>;
};

const EXTENSIONS: Record<string, string> = {
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
  ".webp": "image/webp", ".avif": "image/avif", ".heic": "image/heic", ".mp4": "video/mp4",
  ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
  ".pdf": "application/pdf",
};
const MAX_CONCURRENCY = 3;
const MAX_ATTEMPTS = 3;

function isUnsafePath(value: string): boolean {
  const lower = value.toLowerCase();
  return lower.startsWith("\\\\.\\") || lower.startsWith("\\\\?\\globalroot") || lower.startsWith("\\\\?\\") || lower.startsWith("\\\\");
}
function relative(root: string, file: string): string {
  const value = path.relative(root, file);
  return value.split(path.sep).join("/");
}
function supportedMime(filename: string): string | undefined {
  return EXTENSIONS[path.extname(filename).toLowerCase()];
}
function safeError(error: unknown): string {
  if (error instanceof Error && /^(?:network|http_[0-9]{3}|file_changed|cancelled|unsupported)$/i.test(error.message)) return error.message;
  return "upload_failed";
}

type InternalJob = { destination: Destination; status: IngestionJobView["status"]; roots: number; items: InternalItem[]; running: boolean };

export class IngestionService {
  private readonly jobs = new Map<string, InternalJob>();
  private readonly changed = new Set<string>();
  private emitTimer?: NodeJS.Timeout;

  constructor(private readonly transport: UploadTransport, private readonly onChanged: (jobId: string) => void, private readonly concurrency = MAX_CONCURRENCY) {}

  async ingestRoots(roots: readonly string[], destination: Destination): Promise<IngestionJobView> {
    if (!roots.length || roots.some(isUnsafePath)) throw new Error("unsupported");
    const jobId = randomUUID();
    const job: InternalJob = { destination, status: "scanning", roots: roots.length, items: [], running: false };
    this.jobs.set(jobId, job);
    this.signal(jobId);
    for (const input of roots) await this.scanRoot(jobId, input);
    await this.hashAndPreflight(jobId);
    if (job.status !== "cancelled") {
      job.status = "ready";
      this.signal(jobId);
      void this.pump(jobId);
    }
    return this.snapshot(jobId);
  }

  snapshot(jobId: string): IngestionJobView {
    const job = this.require(jobId);
    const items = job.items.map(({ absolutePath, rootPath, mtimeMs, abort, sha256, ...view }) => view);
    const count = (status: IngestionItemStatus) => job.items.filter(item => item.status === status).length;
    return { id: jobId, status: job.status, rootCount: job.roots, discovered: job.items.length,
      supported: job.items.filter(item => item.status !== "unsupported").length, duplicates: count("duplicate"),
      completed: count("completed"), failed: count("failed"), skipped: count("duplicate") + count("unsupported"),
      uploading: count("uploading"), items };
  }

  pause(jobId: string): IngestionJobView {
    const job = this.require(jobId); job.status = "paused";
    for (const item of job.items.filter(item => item.status === "uploading")) { item.abort?.abort(); item.status = "paused"; }
    for (const item of job.items.filter(item => item.status === "ready")) item.status = "paused";
    this.signal(jobId); return this.snapshot(jobId);
  }
  resume(jobId: string): IngestionJobView {
    const job = this.require(jobId); if (job.status === "cancelled") return this.snapshot(jobId);
    job.status = "ready"; for (const item of job.items.filter(item => item.status === "paused")) item.status = "ready";
    this.signal(jobId); void this.pump(jobId); return this.snapshot(jobId);
  }
  cancel(jobId: string): IngestionJobView {
    const job = this.require(jobId); job.status = "cancelled";
    for (const item of job.items) { if (!["completed", "duplicate", "unsupported"].includes(item.status)) { item.abort?.abort(); item.status = "cancelled"; } }
    this.signal(jobId); return this.snapshot(jobId);
  }
  retry(jobId: string, itemId: string): IngestionJobView {
    const job = this.require(jobId); const item = job.items.find(value => value.id === itemId);
    if (!item || item.status !== "failed") throw new Error("unsupported");
    item.errorCode = undefined; item.attempts = 0; item.status = job.status === "paused" ? "paused" : "ready";
    this.signal(jobId); if (job.status === "ready") void this.pump(jobId); return this.snapshot(jobId);
  }

  private async scanRoot(jobId: string, input: string): Promise<void> {
    const job = this.require(jobId);
    let root: string;
    try { root = await realpath(input); } catch { return; }
    if (isUnsafePath(root)) return;
    const info = await lstat(root).catch(() => undefined);
    if (!info || info.isSymbolicLink()) return;
    if (info.isFile()) { this.addFile(jobId, root, path.dirname(root), info.size, info.mtimeMs); return; }
    if (!info.isDirectory()) return;
    const walk = async (directory: string): Promise<void> => {
      if (job.status === "cancelled") return;
      const entries = await readdir(directory, { withFileTypes: true }).catch(() => []);
      entries.sort((a, b) => a.name.localeCompare(b.name));
      for (const entry of entries) {
        if (entry.isSymbolicLink()) continue;
        const candidate = path.join(directory, entry.name);
        if (entry.isDirectory()) await walk(candidate);
        else if (entry.isFile()) {
          const stat = await lstat(candidate).catch(() => undefined);
          if (stat?.isFile()) this.addFile(jobId, candidate, root, stat.size, stat.mtimeMs);
        }
      }
    };
    await walk(root);
  }

  private addFile(jobId: string, absolutePath: string, rootPath: string, size: number, mtimeMs: number): void {
    const job = this.require(jobId); const filename = path.basename(absolutePath); const mimeType = supportedMime(filename);
    job.items.push({ id: randomUUID(), jobId, absolutePath, rootPath, filename, relativePath: relative(rootPath, absolutePath),
      mimeType: mimeType || "application/octet-stream", size, mtimeMs, status: mimeType ? "scanning" : "unsupported", bytesUploaded: 0, attempts: 0 });
    this.signal(jobId);
  }

  private async hashAndPreflight(jobId: string): Promise<void> {
    const job = this.require(jobId); const candidates = job.items.filter(item => item.status === "scanning");
    for (const item of candidates) {
      if (job.status === "cancelled") return;
      item.status = "hashing"; this.signal(jobId);
      try { item.sha256 = await this.hash(item); item.status = "ready"; } catch (error) { item.status = "failed"; item.errorCode = safeError(error); }
      this.signal(jobId);
    }
    const ready = job.items.filter(item => item.status === "ready" && item.sha256);
    for (let start = 0; start < ready.length; start += 500) {
      const duplicateHashes = await this.transport.preflight(ready.slice(start, start + 500).map(item => item.sha256!));
      for (const item of ready.slice(start, start + 500)) if (item.sha256 && duplicateHashes.has(item.sha256)) item.status = "duplicate";
      this.signal(jobId);
    }
  }

  private hash(item: InternalItem): Promise<string> {
    return new Promise((resolve, reject) => {
      const digest = createHash("sha256"); const source = createReadStream(item.absolutePath);
      source.on("data", chunk => digest.update(chunk));
      source.once("error", () => reject(new Error("file_changed")));
      source.once("end", async () => {
        const stat = await lstat(item.absolutePath).catch(() => undefined);
        if (!stat || !stat.isFile() || stat.size !== item.size || stat.mtimeMs !== item.mtimeMs) reject(new Error("file_changed"));
        else resolve(digest.digest("hex"));
      });
    });
  }

  private async pump(jobId: string): Promise<void> {
    const job = this.require(jobId); if (job.running || job.status !== "ready") return; job.running = true;
    try {
      while (job.status === "ready") {
        const active = job.items.filter(item => item.status === "uploading").length;
        const next = job.items.filter(item => item.status === "ready").slice(0, Math.max(0, this.concurrency - active));
        if (!next.length) break;
        await Promise.all(next.map(item => this.upload(jobId, item)));
      }
      if (job.status === "ready" && !job.items.some(item => ["ready", "uploading", "hashing", "scanning"].includes(item.status))) {
        job.status = job.items.some(item => item.status === "failed") ? "failed" : "completed"; this.signal(jobId);
      }
    } finally { job.running = false; }
  }
  private async upload(jobId: string, item: InternalItem): Promise<void> {
    const job = this.require(jobId); item.status = "uploading"; item.abort = new AbortController(); this.signal(jobId);
    try {
      const now = await lstat(item.absolutePath);
      if (!now.isFile() || now.size !== item.size || now.mtimeMs !== item.mtimeMs) throw new Error("file_changed");
      item.attempts += 1;
      await this.transport.upload({ ...item, destination: job.destination }, item.abort.signal, bytes => { item.bytesUploaded = Math.max(item.bytesUploaded, bytes); this.signal(jobId); });
      if (!item.abort.signal.aborted) item.status = "completed";
    } catch (error) {
      if (item.abort?.signal.aborted) { if (job.status !== "cancelled") item.status = "paused"; }
      else if (item.attempts < MAX_ATTEMPTS && /^(network|http_429|http_5\d\d)$/i.test(safeError(error))) {
        item.status = "ready"; await new Promise(resolve => setTimeout(resolve, 150 * 2 ** item.attempts));
      } else { item.status = "failed"; item.errorCode = safeError(error); }
    } finally { item.abort = undefined; this.signal(jobId); }
  }
  private require(jobId: string) { const job = this.jobs.get(jobId); if (!job) throw new Error("unsupported"); return job; }
  private signal(jobId: string): void {
    this.changed.add(jobId); if (this.emitTimer) return;
    this.emitTimer = setTimeout(() => { const ids = [...this.changed]; this.changed.clear(); this.emitTimer = undefined; ids.forEach(id => this.onChanged(id)); }, 125);
  }
}
