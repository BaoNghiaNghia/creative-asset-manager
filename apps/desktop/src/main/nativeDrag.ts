import { createHash, randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  utimes,
  writeFile,
} from "node:fs/promises";
import { basename, extname, join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

export type NativeDragProvider = "google-drive" | "onedrive" | "sharepoint";

export type NativeDragAssetRequest = {
  id: string;
  name: string;
  mimeType: string;
  provider: NativeDragProvider;
  externalSourceId?: string;
  modifiedAt?: string;
  size?: number;
};

export type PreparedNativeDrag = {
  files: string[];
  iconPath?: string;
  cacheHits: number;
  cacheMisses: number;
  totalBytes: number;
  downloadedBytes: number;
};

export type NativeDragPrepareOptions = {
  revalidateBeforeUse?: boolean;
};

export type NativeDragServiceOptions = {
  cacheTtlMs?: number;
  cacheMaxBytes?: number;
  cleanupIntervalMs?: number;
  downloadConcurrency?: number;
  accessConcurrency?: number;
  cacheEvictionGraceMs?: number;
};

type SessionLike = {
  fetch(input: string, init?: RequestInit): Promise<Response>;
};

type MaterializedOriginal = {
  path: string;
  cacheHit: boolean;
  bytes: number;
};

type CacheDirectory = {
  key: string;
  path: string;
  bytes: number;
  mtimeMs: number;
};

const DEFAULT_CACHE_TTL_MS = 30 * 60 * 1000;
const DEFAULT_CACHE_MAX_BYTES = 10 * 1024 * 1024 * 1024;
const DEFAULT_CLEANUP_INTERVAL_MS = 5 * 60 * 1000;
const DEFAULT_DOWNLOAD_CONCURRENCY = 3;
const DEFAULT_ACCESS_CONCURRENCY = 8;
const CACHE_EVICTION_GRACE_MS = 2 * 60 * 1000;
const MAX_DRAG_FILES = 100;
const MAX_FILENAME_LENGTH = 180;
const VERSION_FILENAME = ".asset-version";
const WINDOWS_RESERVED_NAMES = new Set([
  "CON", "PRN", "AUX", "NUL",
  "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
  "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
]);

class AsyncLimiter {
  private active = 0;
  private readonly pending: Array<() => void> = [];

  constructor(private readonly limit: number) {}

  run<T>(work: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const execute = () => {
        this.active += 1;
        void work()
          .then(resolve, reject)
          .finally(() => {
            this.active -= 1;
            this.drain();
          });
      };
      this.pending.push(execute);
      this.drain();
    });
  }

  private drain(): void {
    while (this.active < this.limit && this.pending.length) {
      this.pending.shift()?.();
    }
  }
}

function positiveOption(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) && (value ?? 0) > 0 ? Math.floor(value as number) : fallback;
}

function appOrigin(value: string): string {
  const url = new URL(value);
  if (!["https:", "http:"].includes(url.protocol)) throw new Error("native_drag_invalid_origin");
  return url.origin;
}

function safeFilename(input: string): string {
  const normalized = basename(input.replace(/\\/g, "/"))
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-")
    .trim()
    .replace(/[. ]+$/g, "");
  let value = normalized || "asset";
  const stem = value.slice(0, Math.max(0, value.length - extname(value).length));
  if (WINDOWS_RESERVED_NAMES.has(stem.toUpperCase())) value = "_" + value;
  if (value.length <= MAX_FILENAME_LENGTH) return value;
  const extension = extname(value).slice(0, 24);
  const keep = Math.max(1, MAX_FILENAME_LENGTH - extension.length);
  return value.slice(0, keep) + extension;
}

function validateItem(item: NativeDragAssetRequest): void {
  if (!item || typeof item !== "object") throw new Error("native_drag_invalid_item");
  if (!["google-drive", "onedrive", "sharepoint"].includes(item.provider)) throw new Error("native_drag_invalid_provider");
  if (!item.id || item.id.length > 2048) throw new Error("native_drag_invalid_id");
  if (!item.name || item.name.length > 1024) throw new Error("native_drag_invalid_name");
  if (!item.mimeType || item.mimeType.length > 255) throw new Error("native_drag_invalid_mime");
  if (item.externalSourceId !== undefined && (!item.externalSourceId || item.externalSourceId.length > 255)) {
    throw new Error("native_drag_invalid_source");
  }
  if (item.modifiedAt !== undefined && item.modifiedAt.length > 128) throw new Error("native_drag_invalid_modified_at");
  if (item.size !== undefined && (!Number.isFinite(item.size) || item.size < 0)) throw new Error("native_drag_invalid_size");
}

function cacheIdentity(item: NativeDragAssetRequest, scope = ""): string {
  return createHash("sha256")
    .update([
      scope,
      item.provider,
      item.externalSourceId || "",
      item.id,
      item.modifiedAt || "",
      String(item.size ?? ""),
    ].join("\u0000"))
    .digest("hex");
}

function assetEndpoint(
  pageUrl: string,
  item: NativeDragAssetRequest,
  endpoint: "media" | "thumbnail" | "access",
): string {
  const encodedId = encodeURIComponent(item.id);
  const pathname = endpoint === "access"
    ? `/api/explorer/media/${encodedId}/access`
    : `/api/explorer/${endpoint}/${encodedId}`;
  const url = new URL(pathname, appOrigin(pageUrl));
  url.searchParams.set("provider", item.provider);
  if (item.externalSourceId) url.searchParams.set("external_source_id", item.externalSourceId);
  return url.toString();
}

async function freshFileSize(
  path: string,
  expectedSize: number | undefined,
  ttlMs: number,
): Promise<number | undefined> {
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.size <= 0 || Date.now() - info.mtimeMs > ttlMs) return undefined;
    if (expectedSize !== undefined && expectedSize > 0 && info.size !== expectedSize) return undefined;
    return info.size;
  } catch {
    return undefined;
  }
}

async function touch(path: string): Promise<void> {
  const now = new Date();
  await utimes(path, now, now).catch(() => undefined);
}

async function readCachedVersion(directory: string): Promise<string | undefined> {
  try {
    const value = (await readFile(join(directory, VERSION_FILENAME), "utf8")).trim();
    return value || undefined;
  } catch {
    return undefined;
  }
}

async function writeCachedVersion(directory: string, version: string | undefined): Promise<void> {
  const path = join(directory, VERSION_FILENAME);
  if (!version) {
    await rm(path, { force: true }).catch(() => undefined);
    return;
  }
  await writeFile(path, version + "\n", { encoding: "utf8", mode: 0o600 });
}

async function directoryUsage(path: string): Promise<number> {
  const entries = await readdir(path, { withFileTypes: true }).catch(() => []);
  let bytes = 0;
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    try {
      bytes += (await lstat(join(path, entry.name))).size;
    } catch {
      // A concurrent cleanup may remove a file between readdir and lstat.
    }
  }
  return bytes;
}

async function writeResponseBody(response: Response, destination: string): Promise<number> {
  if (!response.ok || !response.body) throw new Error(`native_drag_http_${response.status}`);
  const partial = `${destination}.part-${randomUUID()}`;
  try {
    const input = Readable.fromWeb(response.body as never);
    await pipeline(input, createWriteStream(partial, { flags: "wx", mode: 0o600 }));
    const info = await stat(partial);
    if (!info.size) throw new Error("native_drag_empty_original");
    await rename(partial, destination);
    return info.size;
  } catch (error) {
    await rm(partial, { force: true }).catch(() => undefined);
    throw error;
  }
}

export class NativeDragService {
  private readonly inflight = new Map<string, Promise<MaterializedOriginal>>();
  private readonly iconInflight = new Map<string, Promise<string | undefined>>();
  private readonly cacheTtlMs: number;
  private readonly cacheMaxBytes: number;
  private readonly cleanupIntervalMs: number;
  private readonly downloadLimiter: AsyncLimiter;
  private readonly accessLimiter: AsyncLimiter;
  private readonly cacheEvictionGraceMs: number;
  private lastCleanupAt = 0;

  constructor(
    private readonly session: SessionLike,
    private readonly pageUrl: () => string,
    private readonly cacheRoot: string,
    options: NativeDragServiceOptions = {},
  ) {
    this.cacheTtlMs = positiveOption(options.cacheTtlMs, DEFAULT_CACHE_TTL_MS);
    this.cacheMaxBytes = positiveOption(options.cacheMaxBytes, DEFAULT_CACHE_MAX_BYTES);
    this.cleanupIntervalMs = positiveOption(options.cleanupIntervalMs, DEFAULT_CLEANUP_INTERVAL_MS);
    this.downloadLimiter = new AsyncLimiter(
      Math.min(8, positiveOption(options.downloadConcurrency, DEFAULT_DOWNLOAD_CONCURRENCY)),
    );
    this.accessLimiter = new AsyncLimiter(
      Math.min(32, positiveOption(options.accessConcurrency, DEFAULT_ACCESS_CONCURRENCY)),
    );
    this.cacheEvictionGraceMs = positiveOption(
      options.cacheEvictionGraceMs,
      CACHE_EVICTION_GRACE_MS,
    );
  }

  async cleanupExpired(protectedKeys: ReadonlySet<string> = new Set()): Promise<void> {
    await this.cleanupCache(protectedKeys);
    this.lastCleanupAt = Date.now();
  }

  async prepare(
    items: NativeDragAssetRequest[],
    options: NativeDragPrepareOptions = {},
  ): Promise<PreparedNativeDrag> {
    if (!Array.isArray(items) || items.length < 1 || items.length > MAX_DRAG_FILES) {
      throw new Error("native_drag_invalid_count");
    }
    items.forEach(validateItem);

    const scope = appOrigin(this.pageUrl());
    const protectedKeys = new Set(items.map(item => cacheIdentity(item, scope)));
    await mkdir(this.cacheRoot, { recursive: true });
    if (Date.now() - this.lastCleanupAt > this.cleanupIntervalMs) {
      await this.cleanupExpired(protectedKeys);
    }

    const revalidateBeforeUse = options.revalidateBeforeUse !== false;
    const prepared = await Promise.all(
      items.map(item => this.prepareOriginal(item, scope, revalidateBeforeUse)),
    );
    const iconPath = await this.prepareIcon(items[0], scope).catch(() => undefined);

    if (prepared.some(item => !item.cacheHit)) {
      await this.cleanupExpired(protectedKeys);
    }

    return {
      files: prepared.map(item => item.path),
      iconPath,
      cacheHits: prepared.filter(item => item.cacheHit).length,
      cacheMisses: prepared.filter(item => !item.cacheHit).length,
      totalBytes: prepared.reduce((total, item) => total + item.bytes, 0),
      downloadedBytes: prepared.reduce(
        (total, item) => total + (item.cacheHit ? 0 : item.bytes),
        0,
      ),
    };
  }

  private async prepareOriginal(
    item: NativeDragAssetRequest,
    scope: string,
    revalidateBeforeUse: boolean,
  ): Promise<MaterializedOriginal> {
    const key = cacheIdentity(item, scope);
    const directory = join(this.cacheRoot, key);
    const path = join(directory, safeFilename(item.name));
    await mkdir(directory, { recursive: true });

    const cachedBytes = await freshFileSize(path, item.size, this.cacheTtlMs);
    if (cachedBytes !== undefined) {
      let current = true;
      if (revalidateBeforeUse) {
        try {
          current = await this.cachedVersionIsCurrent(item, directory);
        } catch (error) {
          await this.invalidateCachedOriginal(path, directory);
          throw error;
        }
      }
      if (current) {
        await Promise.all([touch(path), touch(directory)]);
        return { path, cacheHit: true, bytes: cachedBytes };
      }
      await this.invalidateCachedOriginal(path, directory);
    }

    const existing = this.inflight.get(key);
    if (existing) {
      const prepared = await existing;
      if (revalidateBeforeUse) {
        let current: boolean;
        try {
          current = await this.cachedVersionIsCurrent(item, directory);
        } catch (error) {
          await this.invalidateCachedOriginal(path, directory);
          throw error;
        }
        if (!current) {
          await this.invalidateCachedOriginal(path, directory);
          return this.prepareOriginal(item, scope, true);
        }
      }
      await Promise.all([touch(prepared.path), touch(directory)]);
      return { ...prepared, cacheHit: true };
    }

    const task = this.downloadLimiter
      .run(() => this.materializeOriginal(item, key, path, directory, revalidateBeforeUse))
      .finally(() => this.inflight.delete(key));
    this.inflight.set(key, task);
    return task;
  }

  private async materializeOriginal(
    item: NativeDragAssetRequest,
    key: string,
    path: string,
    directory: string,
    revalidateBeforeUse: boolean,
  ): Promise<MaterializedOriginal> {
    const cachedBytes = await freshFileSize(path, item.size, this.cacheTtlMs);
    if (cachedBytes !== undefined) {
      let current = true;
      if (revalidateBeforeUse) {
        try {
          current = await this.cachedVersionIsCurrent(item, directory);
        } catch (error) {
          await this.invalidateCachedOriginal(path, directory);
          throw error;
        }
      }
      if (current) {
        await Promise.all([touch(path), touch(directory)]);
        return { path, cacheHit: true, bytes: cachedBytes };
      }
      await this.invalidateCachedOriginal(path, directory);
    }

    await rm(path, { force: true }).catch(() => undefined);
    const response = await this.session.fetch(
      assetEndpoint(this.pageUrl(), item, "media"),
      {
        method: "GET",
        cache: "no-store",
        headers: { "accept-encoding": "identity" },
      },
    );

    const expectedSize = item.size && item.size > 0 ? item.size : undefined;
    const contentLength = Number(response.headers.get("content-length") || "");
    if (
      response.ok
      && expectedSize !== undefined
      && Number.isFinite(contentLength)
      && contentLength > 0
      && contentLength !== expectedSize
    ) {
      await response.body?.cancel().catch(() => undefined);
      throw new Error("native_drag_original_size_mismatch");
    }

    const downloadedVersion = response.headers.get("x-cam-asset-version") || undefined;
    const downloadedBytes = await writeResponseBody(response, path);
    if (expectedSize !== undefined && downloadedBytes !== expectedSize) {
      await this.invalidateCachedOriginal(path, directory);
      throw new Error("native_drag_original_size_mismatch");
    }

    let effectiveVersion = downloadedVersion;
    if (revalidateBeforeUse) {
      try {
        const currentVersion = await this.authorizeCachedItem(item);
        if (downloadedVersion && currentVersion && downloadedVersion !== currentVersion) {
          await this.invalidateCachedOriginal(path, directory);
          throw new Error("native_drag_original_version_mismatch");
        }
        effectiveVersion = currentVersion || downloadedVersion;
      } catch (error) {
        await this.invalidateCachedOriginal(path, directory);
        throw error;
      }
    }
    await writeCachedVersion(directory, effectiveVersion);
    await touch(directory);
    return { path, cacheHit: false, bytes: downloadedBytes };
  }

  private authorizeCachedItem(item: NativeDragAssetRequest): Promise<string | undefined> {
    return this.accessLimiter.run(async () => {
      const response = await this.session.fetch(
        assetEndpoint(this.pageUrl(), item, "access"),
        { method: "GET", cache: "no-store" },
      );
      if (!response.ok) {
        await response.body?.cancel().catch(() => undefined);
        throw new Error(`native_drag_access_http_${response.status}`);
      }
      const version = response.headers.get("x-cam-asset-version") || undefined;
      await response.body?.cancel().catch(() => undefined);
      return version;
    });
  }

  private async cachedVersionIsCurrent(
    item: NativeDragAssetRequest,
    directory: string,
  ): Promise<boolean> {
    const currentVersion = await this.authorizeCachedItem(item);
    if (!currentVersion) return true;
    return (await readCachedVersion(directory)) === currentVersion;
  }

  private async invalidateCachedOriginal(path: string, directory: string): Promise<void> {
    await Promise.all([
      rm(path, { force: true }).catch(() => undefined),
      rm(join(directory, VERSION_FILENAME), { force: true }).catch(() => undefined),
    ]);
  }

  private prepareIcon(
    item: NativeDragAssetRequest,
    scope: string,
  ): Promise<string | undefined> {
    const key = cacheIdentity(item, scope);
    const existing = this.iconInflight.get(key);
    if (existing) return existing;
    const task = this.materializeIcon(item, key)
      .finally(() => this.iconInflight.delete(key));
    this.iconInflight.set(key, task);
    return task;
  }

  private async materializeIcon(
    item: NativeDragAssetRequest,
    key: string,
  ): Promise<string | undefined> {
    const directory = join(this.cacheRoot, key);
    const path = join(directory, "drag-preview.img");
    if (await freshFileSize(path, undefined, this.cacheTtlMs)) {
      await Promise.all([touch(path), touch(directory)]);
      return path;
    }
    await mkdir(directory, { recursive: true });
    const response = await this.session.fetch(
      assetEndpoint(this.pageUrl(), item, "thumbnail"),
      { method: "GET", cache: "no-store" },
    );
    if (!response.ok || !response.body) return undefined;
    await writeResponseBody(response, path);
    await touch(directory);
    return path;
  }

  private async cleanupCache(protectedKeys: ReadonlySet<string>): Promise<void> {
    await mkdir(this.cacheRoot, { recursive: true });
    const entries = await readdir(this.cacheRoot, { withFileTypes: true }).catch(() => []);
    const now = Date.now();
    const activeProtectedKeys = new Set([...protectedKeys, ...this.inflight.keys()]);
    const candidates: CacheDirectory[] = [];

    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const path = join(this.cacheRoot, entry.name);
      try {
        const info = await lstat(path);
        if (!info.isDirectory()) continue;
        if (now - info.mtimeMs > this.cacheTtlMs && !activeProtectedKeys.has(entry.name)) {
          await rm(path, { recursive: true, force: true });
          continue;
        }
        candidates.push({
          key: entry.name,
          path,
          bytes: await directoryUsage(path),
          mtimeMs: info.mtimeMs,
        });
      } catch {
        if (!activeProtectedKeys.has(entry.name)) {
          await rm(path, { recursive: true, force: true }).catch(() => undefined);
        }
      }
    }

    let totalBytes = candidates.reduce((total, entry) => total + entry.bytes, 0);
    if (totalBytes <= this.cacheMaxBytes) return;

    candidates.sort((left, right) => left.mtimeMs - right.mtimeMs);
    for (const entry of candidates) {
      if (totalBytes <= this.cacheMaxBytes) break;
      if (activeProtectedKeys.has(entry.key)) continue;
      if (now - entry.mtimeMs < this.cacheEvictionGraceMs) continue;
      await rm(entry.path, { recursive: true, force: true }).catch(() => undefined);
      totalBytes -= entry.bytes;
    }
  }
}

export const nativeDragInternals = {
  assetEndpoint,
  cacheIdentity,
  safeFilename,
};
