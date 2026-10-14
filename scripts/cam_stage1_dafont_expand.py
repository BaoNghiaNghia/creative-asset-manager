#!/usr/bin/env python3
"""Provision real, verified SIL-OFL DaFont families for Stage 1 (offline admin tool).

Never run from an image-generation request. The normal worker uses ONLY the
approved local registry. Downloads occur solely when explicitly executed by an
administrator. All ZIPs/fonts/licenses are verified before they enter the pool.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import io
import json
from pathlib import Path
import re
import subprocess
import time
import zipfile

from PIL import ImageFont

ROOT = Path(__file__).resolve().parents[1]
SKILL = "gatorhats-stage1-six-designs"
BUNDLES = (
    ROOT / "apps/api/managed_skills" / SKILL,
    ROOT / "deploy/codex/skills" / SKILL,
)
# Category pages are filtered to DaFont's Public domain / GPL / OFL class.
# The ZIP check below is stricter: only actual SIL OFL 1.1 text passes.
SOURCES = (
    ("ggbotnet.d8252", "display", "independent"),
    ("theme.php?cat=501", "sans", "sans-serif"),
    ("theme.php?cat=502", "serif", "serif"),
    ("theme.php?cat=504", "display", "mixed"),
    ("theme.php?cat=603", "script", "handwriting"),
    ("theme.php?cat=503", "sans", "monospace"),
    ("theme.php?cat=604", "script", "brush"),
    ("theme.php?cat=201", "display", "cartoon"),
    ("theme.php?cat=202", "display", "comic"),
    ("theme.php?cat=203", "display", "groovy"),
    ("theme.php?cat=204", "display", "retro"),
    ("theme.php?cat=207", "display", "old-school"),
    ("theme.php?cat=605", "script", "handwriting"),
    ("theme.php?cat=602", "script", "cursive"),
)
FONT_ROW = re.compile(
    r'<div class="lv1left[^"]*">.*?'
    r'<a href="([a-z0-9-]+\.font[^"]*)"><strong>([^<]+)</strong>.*?'
    r'<a class="dl"[^>]*href="//dl\.dafont\.com/dl/\?f=([^"]+)"',
    re.I | re.S,
)
INVALID_STYLE = re.compile(r'(?:italic|oblique|thin|ultra.?light|hairline|outline|dingbat|symbol|icon|ornament)', re.I)
FONT_EXT = (".ttf", ".otf")


def download(url: str, max_bytes: int) -> bytes:
    if not url.startswith(("https://www.dafont.com/", "https://dl.dafont.com/")):
        raise ValueError("Untrusted DaFont URL")
    result = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--retry", "2", "--retry-delay", "1", "--max-time", "22", url],
        capture_output=True, check=False, timeout=80,
    )
    if result.returncode or not result.stdout or len(result.stdout) > max_bytes:
        raise ValueError(f"DaFont response failed or outside size limits (exit={result.returncode}, bytes={len(result.stdout)})")
    return result.stdout


def candidates_from_page(data: bytes, category: str, group: str):
    text = data.decode("utf-8", errors="replace")
    result = []
    for link, name, zip_name in FONT_ROW.findall(text):
        zip_name = html.unescape(zip_name)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{2,75}", zip_name):
            continue
        display_name = html.unescape(name).strip()
        if not display_name or len(display_name) > 110:
            continue
        result.append({
            "font": display_name, "url": "https://www.dafont.com/" + link.split("?")[0],
            "zip_name": zip_name, "group": group, "category": category,
        })
    return result


def extract_approved_zip(blob: bytes):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos = archive.infolist()
        if len(infos) > 120 or sum(i.file_size for i in infos) > 24_000_000:
            raise ValueError("Archive exceeds safe file or decompression limits")
        license_candidates = sorted(
            (i for i in infos if not i.is_dir() and i.file_size <= 80_000
             and i.filename.lower().endswith((".txt", ".md", ".license"))),
            key=lambda i: (not any(x in i.filename.casefold() for x in ("ofl", "license")), i.file_size),
        )
        license_data = None
        for entry in license_candidates:
            data = archive.read(entry)
            upper = data.decode("utf-8", errors="replace").upper()
            if ("SIL OPEN FONT LICENSE" in upper and "VERSION 1.1" in upper
                    and ("PREAMBLE" in upper or "PERMISSION & CONDITIONS" in upper)):
                license_data = data
                break
        if license_data is None:
            raise ValueError("No bundled full SIL Open Font License 1.1")
        options = []
        for entry in infos:
            if entry.is_dir() or not entry.filename.lower().endswith(FONT_EXT):
                continue
            if not 18_000 <= entry.file_size <= 2_800_000:
                continue
            name = entry.filename.rsplit("/", 1)[-1]
            if INVALID_STYLE.search(name):
                continue
            label = name.lower()
            priority = (
                0 if ("regular" in label or "normal" in label) else
                1 if "medium" in label else 2 if "bold" in label else 3
            )
            options.append((priority, entry.file_size, entry))
        for _, _, entry in sorted(options, key=lambda t: (t[0], t[1])):
            data = archive.read(entry)
            try:
                font = ImageFont.truetype(io.BytesIO(data), 85)
                # Reject fonts lacking basic Latin letters/numerals or
                # unusual zero-width decorative fonts inappropriate for stitches.
                for sample in ("ABCDE", "GATOR HATS", "2026", "LOVE"):
                    bounds = font.getbbox(sample)
                    if bounds is None or bounds[2] - bounds[0] < 20 or bounds[3] - bounds[1] < 12:
                        raise ValueError("Missing basic uppercase glyphs")
                font.getmask("ABC 123!").getbbox()
            except Exception:
                continue
            return data, license_data, entry.filename.lower().endswith(".otf")
        raise ValueError("No legible embroidery-safe TTF/OTF in archive")


def save_registry(doc: dict):
    payload = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    for bundle in BUNDLES:
        target = bundle / "font_registry.json"
        temp = target.with_name(".font_registry.pending.json")
        temp.write_bytes(payload)
        temp.replace(target)


def collect_candidates(delay: float, max_pages: int):
    seen = set()
    collected = []
    # Interleave source pages for broader styles rather than 200 from one author.
    for page in range(1, max_pages + 1):
        for spec, category, group in SOURCES:
            url = "https://www.dafont.com/" + spec
            separator = "&" if "?" in spec else "?"
            url += f"{separator}l%5B%5D=10&fpp=50&page={page}"
            try:
                rows = candidates_from_page(download(url, 1_400_000), category, group)
            except (ValueError, subprocess.TimeoutExpired) as exc:
                print(f"CATALOG SKIP {spec} page={page}: {exc}", flush=True)
                rows = []
            for row in rows:
                name = row["font"].casefold()
                if name not in seen:
                    seen.add(name)
                    collected.append(row)
            time.sleep(delay)
    return collected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=7)
    parser.add_argument("--max-attempts", type=int, default=740)
    parser.add_argument("--delay", type=float, default=0.20)
    args = parser.parse_args()
    if not 24 <= args.target <= 300:
        parser.error("Target must be between 24 and 300 distinct families")
    document = json.loads((BUNDLES[0] / "font_registry.json").read_text(encoding="utf-8"))
    approved = document["fonts"]
    seen_names = {row["font"].casefold() for row in approved}
    seen_hashes = {row["sha256"] for row in approved}
    print(f"START currently approved={len(approved)} target={args.target}", flush=True)
    if len(approved) >= args.target:
        return
    candidates = collect_candidates(args.delay, args.max_pages)
    print(f"COLLECTED {len(candidates)} different DaFont candidate families", flush=True)
    # Resume after the last committed download to avoid re-fetching rejected
    # ZIPs on interrupted long import runs. Source listing order is stable
    # within a single catalog revision; missing markers are safely ignored.
    if approved and str(approved[-1].get("file", "")).startswith("dafont_"):
        last_name = approved[-1]["font"].casefold()
        last_index = next(
            (idx for idx, item in enumerate(candidates)
             if item["font"].casefold() == last_name), None,
        )
        if last_index is not None:
            candidates = candidates[last_index + 1:]
            print(f"RESUMED AFTER {approved[-1]['font']}; remaining candidates={len(candidates)}", flush=True)
    rejected = Counter()
    attempts = 0
    for item in candidates:
        if len(approved) >= args.target or attempts >= args.max_attempts:
            break
        if item["font"].casefold() in seen_names:
            continue
        attempts += 1
        try:
            raw = download("https://dl.dafont.com/dl/?f=" + item["zip_name"], 6_000_000)
            font_bytes, license_bytes, otf = extract_approved_zip(raw)
            font_hash = hashlib.sha256(font_bytes).hexdigest()
            if font_hash in seen_hashes:
                rejected["same-font-bytes"] += 1
                continue
            slug = item["zip_name"].lower().replace("-", "_")
            filename = "dafont_" + slug + (".otf" if otf else ".ttf")
            license_name = "dafont_" + slug + "_OFL.txt"
            for bundle in BUNDLES:
                folder = bundle / "fonts"
                if (folder / filename).exists():
                    raise ValueError("Font file collision for " + filename)
            for bundle in BUNDLES:
                (bundle / "fonts" / filename).write_bytes(font_bytes)
                normalized_license = b"\n".join(
                    line.rstrip(b" \t") for line in
                    license_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
                )
                (bundle / "fonts" / license_name).write_bytes(
                    normalized_license.rstrip(b"\n") + b"\n"
                )
            approved.append({
                "font": item["font"], "group": item["group"],
                "category": item["category"], "score": 7 if item["category"] == "script" else 8,
                "file": filename, "license_file": license_name, "license": "SIL OFL 1.1",
                "license_status": "approved", "source": "DaFont", "url": item["url"],
                "sha256": font_hash, "enabled": True, "embroidery_safe": True,
            })
            seen_names.add(item["font"].casefold())
            seen_hashes.add(font_hash)
            # Incremental, transactional registry means interruption can resume.
            save_registry(document)
            if len(approved) % 10 == 0 or len(approved) >= args.target:
                print(f"APPROVED {len(approved)}/{args.target} attempts={attempts} rejected={dict(rejected)}", flush=True)
        except (ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
            rejected[type(exc).__name__ + ": " + str(exc)[:80]] += 1
        time.sleep(args.delay)
    print(f"FINISHED approved={len(approved)} target={args.target} attempts={attempts} rejected={dict(rejected)}", flush=True)
    if len(approved) < args.target:
        raise SystemExit("Not enough license-verified, renderable font files; pool remains at actual count")


if __name__ == "__main__":
    main()
