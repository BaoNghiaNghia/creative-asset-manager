import { cacheKeyFromPath } from "./path";

const encoder = new TextEncoder();
const SIGNATURE_PATTERN = /^[A-Za-z0-9_-]{43}$/;

export function canonicalReadMessage(pathname: string, expiresAt: number): string {
  if (cacheKeyFromPath(pathname) === null ||
      !Number.isSafeInteger(expiresAt) || expiresAt <= 0) {
    throw new TypeError("Invalid video ticket");
  }
  return `v1\nread\n${pathname}\n${expiresAt}`;
}

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function decodeSignature(value: string): Uint8Array<ArrayBuffer> | null {
  if (!SIGNATURE_PATTERN.test(value)) return null;
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=";
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return bytes.length === 32 && base64url(bytes) === value ? bytes : null;
  } catch {
    return null;
  }
}

async function signingKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"],
  );
}

export async function signReadPath(secret: string, pathname: string, expiresAt: number): Promise<string> {
  const signature = await crypto.subtle.sign(
    "HMAC", await signingKey(secret), encoder.encode(canonicalReadMessage(pathname, expiresAt)),
  );
  return base64url(new Uint8Array(signature));
}

export async function verifyReadPath(
  secret: string, pathname: string, expiresAt: number, signature: string,
): Promise<boolean> {
  const bytes = decodeSignature(signature);
  if (bytes === null) return false;
  try {
    return await crypto.subtle.verify(
      "HMAC", await signingKey(secret), bytes,
      encoder.encode(canonicalReadMessage(pathname, expiresAt)),
    );
  } catch {
    return false;
  }
}
