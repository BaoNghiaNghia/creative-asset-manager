#!/usr/bin/env python3
"""Backfill all eligible Picked design ColorMatch V9 metadata.

Default is read-only dry-run. --apply updates ONLY the color_match JSON of
existing, completed and still-Picked jobs. Original PNGs are never rewritten,
uploaded, regenerated or downloaded when an already-verified Drive thumbnail
exists. All Drive bytes are fetched through CAM's private authenticated storage
provider and SHA256-checked. A bounded stream avoids loading every job at once.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
ENV_FILE = Path("/etc/creative-asset-manager/production.env")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ASSET = re.compile(r"[0-9a-f]{24}\Z")
_DRIVE = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")
_JOB = re.compile(r"[a-f0-9-]{36}\Z")
_MAX_SOURCE_BYTES = 80 * 1024 * 1024
_MAX_PIXELS = 30_000_000
_MAX_THUMB_BYTES = 2 * 1024 * 1024
_MAX_ANALYSIS_ORIGINAL_BYTES = 8 * 1024 * 1024
_TARGET_VERSION = "colormatch-sms-c1717-v10"


@dataclass(frozen=True)
class Target:
    job_id: str
    asset_id: str
    kind: str
    digest: str
    provider: str
    local_path: Path | None = None
    drive_thumbnail_id: str | None = None
    thumbnail_sha256: str | None = None
    drive_original_id: str | None = None
    original_size: int | None = None


def fresh(row: dict) -> bool:
    """True only for SHA-bound, validated V10 role-aware rankings."""
    from app.modules.etsy_spy.design_artifacts import _validated_color_match
    meta = row.get("color_match")
    if not isinstance(meta, dict) or meta.get("version") != _TARGET_VERSION:
        return False
    digest = row.get("output_sha256")
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        return False
    projected = _validated_color_match(row, digest)
    if projected is None:
        return False
    colors = projected["recommended"]
    # Refresh the old V10 empty shortlists once after preview calibration;
    # preserve valid existing nonempty choices and stop retrying truly
    # unreadable artwork when the calibrated algorithm still yields none.
    analysis = meta.get("analysis") if isinstance(meta.get("analysis"), dict) else {}
    # V10.1 changed the hard-veto from pigment-wide minima to confirmed text
    # regions and robust illustration visibility. Refresh both empty AND
    # nonempty V10 recommendations once; the marker makes this idempotent.
    if analysis.get("rank_policy") != "text-roi-soft-art-v4":
        return False
    return (len(colors) <= 10 and
            set(colors).issubset(set(projected["eligible_colors"]) | set(projected["review_colors"])) and
            set(colors).isdisjoint(projected["unsafe_colors"]))


def candidate_records(job) -> list[Target]:
    """Only versions needing update; never trust arbitrary storage paths/URLs."""
    if not isinstance(job.id, str) or not _JOB.fullmatch(job.id):
        return []
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    # Drive-only installations must not depend on local PNG root configuration.
    source_root = os.getenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", "")
    local_source = None
    if source_root and Path(source_root).is_absolute():
        from app.modules.etsy_spy.image_agent import _storage_path
        local_source = _storage_path(job.tenant_id, job.id)
    results = []
    for row in payload.get("design_assets", []) if isinstance(payload.get("design_assets"), list) else []:
        if not isinstance(row, dict):
            continue
        asset_id, digest = row.get("id"), row.get("output_sha256")
        if (not isinstance(asset_id, str) or not _ASSET.fullmatch(asset_id)
            or not isinstance(digest, str) or not _SHA.fullmatch(digest)
            or fresh(row)):
            continue
        if row.get("storage_provider") == "google_drive_managed":
            remote, thumb_sha = row.get("drive_thumbnail_id"), row.get("thumbnail_sha256")
            results.append(Target(
                job.id, asset_id, "mapped", digest, "drive",
                drive_thumbnail_id=remote if isinstance(remote, str) and _DRIVE.fullmatch(remote) else None,
                thumbnail_sha256=thumb_sha if isinstance(thumb_sha, str) and _SHA.fullmatch(thumb_sha) else None,
                drive_original_id=(row.get("drive_file_id")
                    if isinstance(row.get("drive_file_id"), str)
                    and _DRIVE.fullmatch(row["drive_file_id"]) else None),
                original_size=(row.get("storage_size_bytes")
                    if type(row.get("storage_size_bytes")) is int else None),
            ))
        elif row.get("storage_provider") in (None, "local"):
            results.append(Target(
                job.id, asset_id, "mapped", digest, "local",
                local_path=(local_source.with_name(f"{job.id}.design.{asset_id}.png")
                            if local_source is not None else None),
            ))
    for kind, field, expected, suffix in (
        ("fast", "result", "needs_review", ""),
        ("candidate", "review_candidate", "review_candidate", ".candidate.png"),
    ):
        row = payload.get(field) if isinstance(payload.get(field), dict) else {}
        digest = row.get("output_sha256")
        if row.get("status") != expected or fresh(row) or not isinstance(digest, str) or not _SHA.fullmatch(digest):
            continue
        path = (local_source if kind == "fast" else local_source.with_suffix(suffix)) if local_source is not None else None
        results.append(Target(job.id, kind, kind, digest, "local", local_path=path))
    return results


def make_local_thumbnail(path: Path | None, digest: str) -> bytes | None:
    """Verify the immutable PNG before producing an in-memory 192px preview."""
    from PIL import Image, UnidentifiedImageError
    if path is None or not path.is_file():
        return None
    size = path.stat().st_size
    if not 57 <= size <= _MAX_SOURCE_BYTES:
        return None
    hashed = hashlib.sha256()
    with path.open("rb") as file:
        for part in iter(lambda: file.read(1024 * 1024), b""):
            hashed.update(part)
    if hashed.hexdigest() != digest:
        return None
    try:
        with Image.open(path) as image:
            if (image.format != "PNG" or image.width < 1 or image.height < 1
                    or image.width * image.height > _MAX_PIXELS):
                return None
            preview = image.convert("RGBA")
            preview.thumbnail((192, 192), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            preview.save(buf, format="PNG")
            return buf.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


async def thumbnail_for(target: Target, tenant_id: str) -> bytes | None:
    if target.provider == "local":
        return await asyncio.to_thread(make_local_thumbnail, target.local_path, target.digest)
    if target.drive_thumbnail_id is None or target.thumbnail_sha256 is None:
        return None
    from app.modules.etsy_spy.imagegen_drive import read_design_from_drive
    # This path uses managed OAuth, SHA check, strict byte limit, no master download.
    result = await read_design_from_drive(
        tenant_id=tenant_id,
        asset_id=f"{target.job_id}:{target.asset_id}:thumbnail",
        remote_id=target.drive_thumbnail_id,
        sha256=target.thumbnail_sha256,
        max_bytes=_MAX_THUMB_BYTES,
    )
    return result if result.startswith(b"\x89PNG\r\n\x1a\n") else None


def _medium_from_png(raw: bytes) -> bytes | None:
    """Bounded analysis-only 512px PNG; master bytes are never modified."""
    from PIL import Image, UnidentifiedImageError
    if not (57 <= len(raw) <= _MAX_ANALYSIS_ORIGINAL_BYTES):
        return None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format != "PNG" or image.width * image.height > _MAX_PIXELS):
                return None
            reduced = image.convert("RGBA")
            reduced.thumbnail((512, 512), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            reduced.save(buf, format="PNG")
            result = buf.getvalue()
            return result if len(result) <= 6 * 1024 * 1024 else None
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def _local_medium(target: Target) -> bytes | None:
    path = target.local_path
    if not path or not path.is_file() or path.stat().st_size > _MAX_ANALYSIS_ORIGINAL_BYTES:
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != target.digest:
        return None
    return _medium_from_png(raw)


async def analysis_preview_for(target: Target, tenant_id: str) -> bytes | None:
    """High-quality scoring only when verified master is small enough.

    Any failure is a safe downgrade to the existing SHA-checked 192px preview.
    The existing Drive thumbnail remains the authoritative minimal evidence.
    """
    if target.provider == "local":
        return await asyncio.to_thread(_local_medium, target)
    if (not target.drive_original_id or not target.original_size
            or target.original_size > _MAX_ANALYSIS_ORIGINAL_BYTES):
        return None
    from app.modules.etsy_spy.imagegen_drive import read_design_from_drive
    try:
        raw = await read_design_from_drive(
            tenant_id=tenant_id,
            asset_id=f"{target.job_id}:{target.asset_id}:original",
            remote_id=target.drive_original_id,
            sha256=target.digest,
            max_bytes=_MAX_ANALYSIS_ORIGINAL_BYTES,
        )
    except Exception:
        # No secrets or remote asset IDs in logs; never skip a valid
        # small preview merely because higher-resolution input is unavailable.
        return None
    return await asyncio.to_thread(_medium_from_png, raw)


def still_same(row: dict, target: Target) -> bool:
    if row.get("output_sha256") != target.digest or fresh(row):
        return False
    if target.kind == "mapped":
        if row.get("id") != target.asset_id:
            return False
        if target.provider == "drive":
            return (row.get("storage_provider") == "google_drive_managed"
                    and row.get("drive_thumbnail_id") == target.drive_thumbnail_id
                    and row.get("thumbnail_sha256") == target.thumbnail_sha256)
        return row.get("storage_provider") in (None, "local")
    return row.get("status") == ("needs_review" if target.kind == "fast" else "review_candidate")


def apply_one(target: Target, tenant_id: str, metadata: dict) -> bool:
    """Lock + recheck Picked, job status, SHA and Drive identity before one JSON commit."""
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.modules.etsy_spy.model import EtsySpyPicked
    from app.modules.processing.model import ProcessingJobModel

    with SessionLocal() as db:
        job = db.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.id == target.job_id,
            ProcessingJobModel.job_type == "etsy_design_extract",
            ProcessingJobModel.status == "completed",
            ProcessingJobModel.cancellation_requested.is_(False),
        ).with_for_update())
        if job is None or db.get(EtsySpyPicked, (tenant_id, job.entity_id)) is None:
            db.rollback()
            return False
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        if target.kind == "mapped":
            assets = payload.get("design_assets")
            if not isinstance(assets, list):
                db.rollback()
                return False
            revised = []
            changed = False
            for row in assets:
                if isinstance(row, dict) and still_same(row, target):
                    revised.append({**row, "color_match": metadata})
                    changed = True
                else:
                    revised.append(row)
            if not changed:
                db.rollback()
                return False
            payload = {**payload, "design_assets": revised}
        else:
            key = "result" if target.kind == "fast" else "review_candidate"
            row = payload.get(key)
            if not isinstance(row, dict) or not still_same(row, target):
                db.rollback()
                return False
            payload = {**payload, key: {**row, "color_match": metadata}}
        job.payload_json = payload
        db.commit()
        return True


async def backfill(args) -> dict:
    from sqlalchemy import select, exists
    from app.core.database import SessionLocal
    from app.modules.processing.model import ProcessingJobModel
    from app.modules.etsy_spy.model import EtsySpyPicked
    from app.modules.etsy_spy.color_match import recommend_garment_colors

    counts: dict[str, int | str] = {
        "mode": "apply" if args.apply else "dry_run",
        "tenant_id": args.tenant_id,
        "scanned_jobs": 0, "already_current": 0,
        "targets_local": 0, "targets_drive": 0,
        "updated_local": 0, "updated_drive": 0,
        "analysis_medium": 0, "analysis_thumbnail_fallback": 0,
        "updated_under_seven": 0, "updated_zero_safe": 0,
        "updated_with_review_colors": 0, "updated_review_color_slots": 0,
        "updated_faded_ink": 0, "updated_text_regions": 0,
        "updated_print_review_required": 0,
        "unavailable_or_mismatch": 0, "skipped": 0, "errors": 0,
    }
    cursor: str | None = None
    while True:
        with SessionLocal() as db:
            stmt = select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == args.tenant_id,
                ProcessingJobModel.job_type == "etsy_design_extract",
                ProcessingJobModel.status == "completed",
                ProcessingJobModel.cancellation_requested.is_(False),
                exists().where(
                    EtsySpyPicked.tenant_id == ProcessingJobModel.tenant_id,
                    EtsySpyPicked.listing_key == ProcessingJobModel.entity_id,
                ),
            )
            if cursor is not None:
                stmt = stmt.where(ProcessingJobModel.id > cursor)
            jobs = db.scalars(stmt.order_by(ProcessingJobModel.id).limit(args.page_size)).all()
            pages = [(job.id, job.tenant_id, job.payload_json) for job in jobs]
            db.rollback()
        if not pages:
            break
        cursor = pages[-1][0]
        for job_id, tenant_id, payload in pages:
            from types import SimpleNamespace
            job = SimpleNamespace(id=job_id, tenant_id=tenant_id, payload_json=payload)
            counts["scanned_jobs"] += 1
            assets = payload.get("design_assets", []) if isinstance(payload, dict) else []
            counts["already_current"] += sum(
                1 for row in assets if isinstance(row, dict) and fresh(row)
            ) if isinstance(assets, list) else 0
            for key in ("result", "review_candidate"):
                old = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(old, dict) and fresh(old):
                    counts["already_current"] += 1
            targets = candidate_records(job)
            # The advisory count does not imply physical source availability.
            for target in targets:
                type_label = target.provider
                counts[f"targets_{type_label}"] += 1
                if not args.apply:
                    continue
                try:
                    preview = await thumbnail_for(target, args.tenant_id)
                    if preview is None:
                        counts["unavailable_or_mismatch"] += 1
                        continue
                    analysis_png = None if args.no_highres else await analysis_preview_for(
                        target, args.tenant_id)
                    if analysis_png is None:
                        counts["analysis_thumbnail_fallback"] += 1
                    else:
                        counts["analysis_medium"] += 1
                    metadata = await asyncio.to_thread(
                        recommend_garment_colors, preview, target.digest,
                        analysis_png=analysis_png)
                    if not metadata:
                        counts["skipped"] += 1
                        continue
                    if await asyncio.to_thread(apply_one, target, args.tenant_id, metadata):
                        counts[f"updated_{type_label}"] += 1
                        region = metadata.get("analysis", {})
                        if len(metadata.get("eligible_colors", [])) < 7:
                            counts["updated_under_seven"] += 1
                        if not metadata.get("eligible_colors"):
                            counts["updated_zero_safe"] += 1
                        risk_count = len(metadata.get("review_colors", []))
                        if risk_count:
                            counts["updated_with_review_colors"] += 1
                            counts["updated_review_color_slots"] += risk_count
                        if region.get("faded_ink"):
                            counts["updated_faded_ink"] += 1
                        if region.get("text_regions", 0) > 0:
                            counts["updated_text_regions"] += 1
                        if region.get("print_review_required"):
                            counts["updated_print_review_required"] += 1
                    else:
                        counts["skipped"] += 1
                except Exception:
                    # Never log credentials, Drive IDs, customer text, or paths.
                    counts["errors"] += 1
                    if counts["errors"] >= args.max_errors:
                        raise RuntimeError("backfill_error_limit_exceeded")
                finally:
                    await asyncio.sleep(args.delay_seconds)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, choices=["creative-assets-prod"])
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--max-errors", type=int, default=12)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-highres", action="store_true",
                        help="Use only the verified Drive thumbnail during high system load")
    args = parser.parse_args()
    if not (1 <= args.page_size <= 200
            and 0.2 <= args.delay_seconds <= 10
            and 1 <= args.max_errors <= 100):
        raise SystemExit("invalid_bounds")
    if os.name != "posix" or not ENV_FILE.is_file():
        raise SystemExit("production_host_required")
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=True)
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("production_postgresql_required")
    try:
        print(json.dumps(asyncio.run(backfill(args)), sort_keys=True))
    except RuntimeError as exc:
        if str(exc) == "backfill_error_limit_exceeded":
            raise SystemExit("backfill_aborted_repeated_source_errors") from None
        raise


if __name__ == "__main__":
    main()