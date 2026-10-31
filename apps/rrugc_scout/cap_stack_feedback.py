"""Conservative, per-claim negative feedback for the Cap Stack Pinterest Scout.

No GPU, model, database or persisted local user profile. Rejected images are
supplied by the authorized tenant's claim. Hashing runs only on bounded resized
Pinterest thumbnails; errors fall back to exact asset / distinctive caption
matching rather than blocking all discoveries.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # Older Windows Scout binaries can still use URL/text safety.
    Image = None
    UnidentifiedImageError = ValueError


# Words shared by nearly *every* in-scope Cap Stack must never become reject
# instructions, even if several rejected Pins use identical generic titles.
STOP_WORDS = frozenset("""
cap caps baseball hat hats stack stacked stacking pile piles photo image pin
pinterest of on a an the and with for to by in from some set collection
picture product snapback trucker visible brim brims two three four five six
photograph shot this these those new ideas fashion style 2 3 4 5 6
""".split())
MAX_NEGATIVE_SEEDS = 24
MAX_CANDIDATE_HASHES = 24
MAX_DOWNLOAD_BYTES = 1_500_000
PER_IMAGE_TIMEOUT_SECONDS = 4
NEGATIVE_PREPARE_SECONDS = 12


def _url_keys(url: str) -> tuple[str, str]:
    """Pin ID or Pinterest rendition-independent CDN asset ID."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or parts.username or parts.password:
        return "", ""
    if host in {"www.pinterest.com", "pinterest.com"}:
        match = re.fullmatch(r"/pin/([0-9]+)/?", parts.path)
        return (match.group(1), "") if match else ("", "")
    if host != "i.pinimg.com":
        return "", ""
    segments = [part for part in parts.path.split("/") if part]
    if len(segments) >= 2 and re.fullmatch(r"(?:originals|[0-9]+x(?:[0-9]+)?(?:_[A-Za-z0-9]+)?)", segments[0], re.I):
        segments = segments[1:]
    return "", "/".join(segments).lower()


def _thumbnail(url: str) -> str:
    parts = urlsplit(str(url or ""))
    if parts.scheme != "https" or (parts.hostname or "").lower() != "i.pinimg.com":
        return ""
    if parts.username or parts.password or parts.port:
        return ""
    _, asset = _url_keys(url)
    if not asset or not re.search(r"\.(?:jpe?g|png|webp)$", asset, re.I):
        return ""
    return urlunsplit(("https", "i.pinimg.com", "/236x/" + asset, "", ""))


def distinctive_words(text: str | None) -> frozenset[str]:
    return frozenset(
        word for word in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(word) >= 4 and word not in STOP_WORDS
    )


def caption_is_near_duplicate(a: frozenset[str], b: frozenset[str]) -> bool:
    # Require a near-identical *descriptive* caption, not only "cap stack".
    return bool(len(a) >= 3 and len(b) >= 3 and
                len(a & b) >= 3 and len(a & b) / len(a | b) >= 0.78)


@dataclass(frozen=True)
class SmallFingerprint:
    # 8 rows x 8 horizontal luminance differences, plus average RGB.
    dhash: int
    rgb: tuple[int, int, int]


def hash_image_bytes(content: bytes) -> SmallFingerprint | None:
    if Image is None or not content or len(content) > MAX_DOWNLOAD_BYTES:
        return None
    try:
        with Image.open(BytesIO(content)) as picture:
            if picture.width * picture.height > 32_000_000:
                return None
            pixels = list(picture.convert("L").resize((9, 8)).getdata())
            dhash = 0
            for y in range(8):
                for x in range(8):
                    dhash = (dhash << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
            channels = picture.convert("RGB").resize((8, 8)).getdata()
            sums = tuple(sum(px[c] for px in channels) // 64 for c in range(3))
            return SmallFingerprint(dhash=dhash, rgb=sums)
    except (ValueError, OSError, UnidentifiedImageError):
        return None


def visually_near_duplicate(a: SmallFingerprint, b: SmallFingerprint) -> bool:
    # A shape match alone can accidentally reject different-colored hats.
    return ((a.dhash ^ b.dhash).bit_count() <= 5
            and sum(abs(x - y) for x, y in zip(a.rgb, b.rgb)) <= 75)


class CapStackFeedbackFilter:
    def __init__(self, task: dict):
        rejected = [row for row in task.get("rejected_seeds", []) if isinstance(row, dict)]
        approved = [row for row in task.get("related_seeds", []) if isinstance(row, dict)]
        self.rejected = rejected[:MAX_NEGATIVE_SEEDS]
        self.bad_pins: set[str] = set()
        self.bad_assets: set[str] = set()
        self.bad_captions: list[frozenset[str]] = []
        self.good_captions = [
            distinctive_words(row.get("alt_text"))
            for row in approved
        ]
        for row in rejected:
            pin, _ = _url_keys(str(row.get("pin_url") or ""))
            _, asset = _url_keys(str(row.get("image_url") or ""))
            if pin:
                self.bad_pins.add(pin)
            if asset:
                self.bad_assets.add(asset)
            words = distinctive_words(row.get("alt_text"))
            if words:
                self.bad_captions.append(words)
        self.hashes: list[SmallFingerprint] = []
        self.hash_cache: dict[str, SmallFingerprint | None] = {}
        self.candidate_hash_budget = MAX_CANDIDATE_HASHES

    def quickly_rejected(self, row) -> bool:
        pin, _ = _url_keys(str(getattr(row, "pin_url", "") or ""))
        _, asset = _url_keys(str(getattr(row, "image_url", "") or ""))
        if pin in self.bad_pins or bool(asset and asset in self.bad_assets):
            return True
        words = distinctive_words(getattr(row, "alt_text", None))
        if not words:
            return False
        if any(caption_is_near_duplicate(words, approved) for approved in self.good_captions):
            return False
        return any(caption_is_near_duplicate(words, bad) for bad in self.bad_captions)

    async def _get_hash(self, client: httpx.AsyncClient, url: str) -> SmallFingerprint | None:
        thumbnail = _thumbnail(url)
        if not thumbnail:
            return None
        if thumbnail in self.hash_cache:
            return self.hash_cache[thumbnail]
        result = None
        try:
            async with client.stream("GET", thumbnail) as response:
                if response.status_code == 200 and response.headers.get("content-type", "").split(";")[0] in {
                    "image/jpeg", "image/png", "image/webp",
                }:
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_DOWNLOAD_BYTES:
                            break
                    if len(chunks) <= MAX_DOWNLOAD_BYTES:
                        result = hash_image_bytes(bytes(chunks))
        except (httpx.HTTPError, ValueError, OSError):
            pass
        self.hash_cache[thumbnail] = result
        return result

    async def prepare(self) -> None:
        """Read a bounded sample of old rejected images once per Scout lease."""
        if Image is None or not self.rejected:
            return
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(PER_IMAGE_TIMEOUT_SECONDS),
            trust_env=False,
        ) as client:
            async def load(row):
                return await self._get_hash(client, str(row.get("image_url") or ""))
            async def bounded():
                semaphore = asyncio.Semaphore(4)
                async def worker(row):
                    async with semaphore:
                        return await load(row)
                return await asyncio.gather(*(worker(row) for row in self.rejected), return_exceptions=True)
            try:
                results = await asyncio.wait_for(bounded(), timeout=NEGATIVE_PREPARE_SECONDS)
                self.hashes = [h for h in results if isinstance(h, SmallFingerprint)]
            except (asyncio.TimeoutError, httpx.HTTPError):
                # URL & caption protection remain active even on Pinterest outage.
                self.hashes = [h for h in self.hash_cache.values() if isinstance(h, SmallFingerprint)]

    async def filter_resolved(self, rows: list) -> tuple[list, list]:
        kept, excluded = [], []
        if not rows:
            return kept, excluded
        if not self.hashes:
            for row in rows:
                (excluded if self.quickly_rejected(row) else kept).append(row)
            return kept, excluded
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(PER_IMAGE_TIMEOUT_SECONDS),
            trust_env=False,
        ) as client:
            for row in rows:
                if self.quickly_rejected(row):
                    excluded.append(row)
                    continue
                if self.candidate_hash_budget > 0:
                    self.candidate_hash_budget -= 1
                    hash_value = await self._get_hash(client, str(getattr(row, "image_url", "") or ""))
                    if hash_value is not None and any(
                        visually_near_duplicate(hash_value, rejected) for rejected in self.hashes
                    ):
                        excluded.append(row)
                        continue
                kept.append(row)
        return kept, excluded
