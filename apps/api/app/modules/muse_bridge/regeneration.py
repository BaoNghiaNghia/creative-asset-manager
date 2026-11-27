"""Operator-requested UGC deletion or contrast repair on the live Muse queue.

No new tables, model invocations, or public Drive credentials. A selected
video is SHA-fenced, verified on Drive, deleted, and queued in one locked
database transaction. Old commands remain historical.
"""
from __future__ import annotations

import re
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .model import CamMuseCommand, CamMuseJob
from .ui import video_ugc_view

ASPECTS = ("9:16", "16:9")
SLUGS = {"9:16": "vertical", "16:9": "horizontal"}
SHA = re.compile(r"^[0-9a-f]{64}$")
DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")


def requested_aspects(job):
    """Explicit missing output work takes precedence over vertical-first."""
    rec = job.output_receipts or {}
    marker = rec.get("videoRegeneration") or {}
    if not isinstance(marker, dict):
        return None
    ratios = marker.get("requiredVideoAspectRatios")
    if not isinstance(ratios, list) or not ratios or any(r not in ASPECTS for r in ratios):
        return None
    from .router import _verified_video_ratios
    if set(ratios) - _verified_video_ratios(job):
        return [r for r in ASPECTS if r in ratios]
    return None


def regeneration_revisions(job):
    rec = job.output_receipts or {}
    revisions = rec.get("videoGenerationRevisionByAspect")
    return {r: revisions[r] for r in ASPECTS
            if isinstance(revisions, dict) and isinstance(revisions.get(r), str)
            and re.fullmatch(r"[0-9a-f]{32}", revisions[r])}


def _assert_latest_compatible(job, kept, muse):
    """Never silently mix incompatible listing-wide generation contracts."""
    if not kept:
        return
    pin = muse._pinned_story_v33(job)
    if muse._story_v33_enabled() and muse._story_v33_allowed_for_job(job):
        lite = muse._story_lite_enabled()
        compatible = bool(pin and muse._story_lite_pin(pin) == lite and all(
            v.get("storyVersion") == muse.STORY_VERSION
            and v.get("storyOutputContract") == (muse.LITE_OUTPUT if lite else muse.STORY_OUTPUT_CONTRACT)
            for v in kept.values()))
    else:
        compatible = bool(muse._pinned_action_story_v1(job) and all(
            v.get("storyActionVersion") == muse.STORY_ACTION_VERSION for v in kept.values()))
    if not compatible:
        raise HTTPException(409, "Video còn lại dùng workflow cũ. Hãy chọn xóa và tạo lại cả hai video để dùng workflow Muse mới nhất.")


async def delete_drive_videos(tenant, listing_id, artwork_sha, selected, *, archive=False):
    """Verify every selected nested Drive object before deleting any of them.

    A 404 on a retry is safe: the DB still owns the immutable receipt ID.
    A mismatched owner, parent, asset ID or SHA is never deleted.
    """
    from app.modules.etsy_spy.imagegen_drive import provider_and_folder, verify_private_drive_destination
    from app.modules.etsy_spy.listing_drive_folders import ROOT_FOLDER_ID
    provider, root = provider_and_folder()
    if root != ROOT_FOLDER_ID:
        raise HTTPException(503, "Video storage root changed")
    await verify_private_drive_destination(provider, root)
    token = await provider.get_access_token()
    targets = []
    for ratio, video in selected.items():
        fid, parent, sha = (str(video.get(k) or "") for k in
                            ("drive_file_id", "drive_folder_id", "sha256"))
        if not DRIVE_ID.fullmatch(fid) or not DRIVE_ID.fullmatch(parent) or not SHA.fullmatch(sha):
            raise HTTPException(409, "Invalid verified video receipt")
        targets.append((fid, parent, sha, f"muse-ugc:{listing_id}:{artwork_sha}:{SLUGS[ratio]}"))
    if len({t[0] for t in targets}) != len(targets):
        raise HTTPException(409, "Shared video file references")
    base = "https://www.googleapis.com/drive/v3/files/"
    try:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                     timeout=httpx.Timeout(30, connect=8)) as client:
            found = []
            for fid, parent, sha, aid in targets:
                response = await client.get(base + fid, params={
                    "supportsAllDrives": "true", "fields": "id,parents,trashed,appProperties"})
                if response.status_code == 404:
                    if archive:
                        raise HTTPException(409, "Rejected original unavailable for reversible archive")
                    continue
                if response.status_code != 200:
                    raise HTTPException(503, "Drive video identity unavailable")
                data = response.json()
                props = data.get("appProperties") or {}
                if (data.get("id") != fid or parent not in (data.get("parents") or [])
                        or props.get("cam_tenant_id") != tenant
                        or props.get("cam_asset_id") != aid or props.get("cam_content_hash") != sha):
                    raise HTTPException(409, "Drive video identity or ownership mismatch")
                if not archive or not data.get("trashed"):
                    found.append(fid)
            for fid in found:
                if archive:
                    response = await client.patch(base + fid, params={
                        "supportsAllDrives": "true", "fields": "id,trashed"},
                        json={"trashed": True})
                    if (response.status_code != 200 or response.json().get("id") != fid
                            or response.json().get("trashed") is not True):
                        raise HTTPException(503, "Drive quarantine incomplete; verify original before retry")
                else:
                    response = await client.delete(base + fid, params={"supportsAllDrives": "true"})
                    if response.status_code not in (200, 204, 404):
                        raise HTTPException(503, "Drive deletion incomplete; retry safely")
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(503, "Drive deletion unavailable; retry safely") from exc


async def regenerate(db: Session, tenant: str, listing_id: str, expected: dict[str, str], *, hero_repair: dict | None = None):
    from . import router as muse
    if (not muse.LISTING_ID.fullmatch(listing_id) or not expected
            or len(expected) > 2 or any(r not in ASPECTS or not SHA.fullmatch(s)
                                       for r, s in expected.items())):
        raise HTTPException(422, "Expected video aspect and SHA are required")
    if hero_repair is not None:
        if (not isinstance(hero_repair, dict) or len(expected) != 1
                or set(hero_repair) != {"colorway", "correctedLocalSha256", "oversizedFlatPrint"}
                or hero_repair["colorway"] not in ("Black", "Pepper", "Navy")
                or not isinstance(hero_repair["correctedLocalSha256"], str)
                or not SHA.fullmatch(hero_repair["correctedLocalSha256"])
                or hero_repair["correctedLocalSha256"] in expected.values()
                or type(hero_repair["oversizedFlatPrint"]) is not bool):
            raise HTTPException(422, "Contrast repair requires one aspect, dark C1717 color and distinct SHA")
    # Match command claim lock order. Reject any active claimed command/job;
    # never clear a worker's active lease or let a stale upload win.
    commands = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant, CamMuseCommand.listing_key == listing_id,
        CamMuseCommand.status.in_(("pending", "claimed")),
    ).order_by(CamMuseCommand.command_id).with_for_update()).all()
    job = db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant, CamMuseJob.listing_key == listing_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not job:
        raise HTTPException(404, "Picked video job not found")
    current_ids = set(db.scalars(select(CamMuseCommand.command_id).where(
        CamMuseCommand.tenant_id == tenant, CamMuseCommand.listing_key == listing_id,
        CamMuseCommand.status.in_(("pending", "claimed")),
    )).all())
    if current_ids != {c.command_id for c in commands}:
        raise HTTPException(409, "Hàng đợi vừa thay đổi. Hãy thử lại.")
    if job.status == "claimed" or any(c.status == "claimed" for c in commands):
        raise HTTPException(409, "Muse đang xử lý listing này. Hãy thử lại khi job kết thúc.")
    old = dict(job.output_receipts or {})
    uploads = dict(old.get("drive_uploads") or {})
    verified = muse._verified_video_ratios(job)
    if any(r not in verified or uploads[r].get("sha256") != sha for r, sha in expected.items()):
        raise HTTPException(409, "Video đã thay đổi hoặc đã được xóa. Hãy tải lại danh sách.")
    if (job.source_state != "ready_for_muse" or not job.artwork_asset_path
            or (muse._qa_record(job) or {}).get("verdict") != "pass"
            or (muse._qa_record(job) or {}).get("artwork_sha256") != job.artwork_sha256):
        raise HTTPException(409, "Native Master hoặc QA ảnh không còn hợp lệ")
    from . import skip_reconciliation as skips
    if old.get("videoCapabilityHold") or muse._automation_soft_hold_active(old) or skips.active(job):
        raise HTTPException(409, "Listing đang bị hold; cần xử lý hold trước khi tạo lại")
    if requested_aspects(job):
        raise HTTPException(409, "Listing đã có yêu cầu tạo lại video")
    selected = {r: uploads[r] for r in ASPECTS if r in expected}
    kept = {r: uploads[r] for r in ASPECTS if r in verified and r not in expected}
    if {v["drive_file_id"] for v in selected.values()} & {v["drive_file_id"] for v in kept.values()}:
        raise HTTPException(409, "Shared video file references")
    _assert_latest_compatible(job, kept, muse)
    original_pin = muse._pinned_story_v33(job)
    if hero_repair is not None:
        if any(c.phase != "create_video" or
               (c.details or {}).get("requiredVideoAspectRatios") != list(selected)
               for c in commands):
            raise HTTPException(409, "Other pending work must not be superseded by this repair")
        if (not muse._story_lite_pin(original_pin)
                or any(v.get("storyOutputContract") != muse.LITE_OUTPUT for v in selected.values())
                or old.get("videoContentQa") != "pending_review"):
            raise HTTPException(409, "Contrast repair requires the pending-review pinned Story Lite video")
        if any((v.get("sha256") == hero_repair["correctedLocalSha256"]) for v in kept.values()):
            raise HTTPException(409, "Corrected reference SHA matches retained sibling")
    now = muse._utc()
    command_id = str(uuid4())
    revision = uuid4().hex
    revisions = regeneration_revisions(job)
    revisions.update({r: revision for r in selected})
    # Preserve verified artwork QA. Keep only retained outputs and their
    # source evidence; obsolete render retries and video QA do not carry over.
    receipts = {"visual_qa": old["visual_qa"],
                "drive_uploads": kept,
                "camLinked": False, "videoContentQa": "pending_review",
                "videoGarmentQa": "pending_independent_visual_review",
                "videoGenerationRevisionByAspect": revisions,
                "videoRegeneration": {
                    "commandId": command_id, "requestedAtUtc": now.isoformat(),
                    "requiredVideoAspectRatios": list(selected),
                    "removedSha256ByAspect": expected,
                }}
    from . import latest_artwork
    for key in (latest_artwork.HISTORY, latest_artwork.CURRENT):
        if key in old:
            receipts[key] = old[key]
    if kept or hero_repair is not None:
        for key in ("hero_sources", "hero_revisions", "story_action_preflight"):
            by_ratio = old.get(key) or {}
            receipts[key] = {r: by_ratio[r] for r in kept if r in by_ratio}
        for key in ("story_contract_v33", "story_action_v1"):
            if isinstance(old.get(key), dict):
                receipts[key] = dict(old[key])
    # Rejected media remains auditable by exact SHA; do not label the retained
    # aspect failed. Its original receipt and pinned hero plan stay unchanged.
    if "rejectedVideoHistoryByAspect" in old:
        receipts["rejectedVideoHistoryByAspect"] = old["rejectedVideoHistoryByAspect"]
    if hero_repair is not None:
        ratio = next(iter(selected))
        reason = "Light ink on light garment; design illegible."
        if hero_repair["oversizedFlatPrint"]:
            reason += " Oversized print and flat overlay appearance."
        history = dict(receipts.get("rejectedVideoHistoryByAspect") or {})
        prior = list(history.get(ratio) or [])
        prior.append({"videoContentQa": "fail", "reasonCode": "HERO_LIGHT_INK_CONTRAST",
                      "reason": reason, "reviewSource": "worker_reported_operator_authorized",
                      "reviewedAtUtc": now.isoformat(), "artworkSha256": job.artwork_sha256,
                      "videoSha256": expected[ratio], "receipt": selected[ratio],
                      "archiveMode": "drive_trash_recoverable", "commandId": command_id})
        history[ratio] = prior
        receipts["rejectedVideoHistoryByAspect"] = history
        receipts["videoContentQa"] = "fail"
        receipts["heroRepairByAspect"] = {ratio: {
            "policyVersion": "CAM_VIDEO_HERO_CONTRAST_REPAIR_V1",
            "commandId": command_id, "artworkSha256": job.artwork_sha256,
            "rejectedVideoSha256": expected[ratio], "requiredColorway": hero_repair["colorway"],
            "localCorrectedVideoSha256Advisory": hero_repair["correctedLocalSha256"],
            "reasonCode": "HERO_LIGHT_INK_CONTRAST", "originalHeroPlan":
                (original_pin.get("heroGenerationPlans") or {}).get(ratio),
            "requiresCorrectedHero": True, "firstInkBelowCollarTargetMm": 150,
            "preserveOriginalStoryCharacterAndScene": True,
        }}
        receipts["videoRegeneration"]["repairPolicy"] = "CAM_VIDEO_HERO_CONTRAST_REPAIR_V1"
    job.output_receipts = receipts
    job.status = "pending"
    job.attempts = 0
    job.lease_hash = job.lease_expires_at = None
    job.submitted_at = job.completed_at = None
    job.updated_at = now
    # A retained current-policy story must use a fresh source ID for the
    # regenerated ratio, while its retained sibling keeps the original plan.
    if (kept or hero_repair is not None) and receipts.get("story_contract_v33"):
        candidate = muse._story_candidate_v33(job)
        if not candidate:
            raise HTTPException(409, "Current Muse story contract unavailable")
        pin = dict(receipts["story_contract_v33"])
        plans = dict(pin.get("heroGenerationPlans") or {})
        for r in selected:
            plans[r] = {f: candidate["aspectPlans"][r][f]
                        for f in ("heroRequestId", "heroSeed", "storyId")}
        pin["heroGenerationPlans"] = plans
        job.output_receipts = {**receipts, "story_contract_v33": pin}
    command = CamMuseCommand(
        tenant_id=tenant, command_id=command_id, listing_key=listing_id,
        action="create_video", request_id="cam-video-regeneration-" + revision,
        status="pending", phase="create_video", artwork_sha256=job.artwork_sha256,
        attempts=0, created_at=now, updated_at=now,
        details={"origin": "etsy-spy-delete-regenerate", "rights_verified": False,
                 "requiredVideoAspectRatios": list(selected),
                 "note": "Explicit operator regeneration: use live videoJobClaim contract, generate fresh output for missing selected aspects.",
                 **({"heroRepairByAspect": receipts["heroRepairByAspect"]} if hero_repair is not None else {})})
    if muse._eligible_phase(db, tenant, job, command) != "create_video":
        raise HTTPException(409, "Listing chưa đủ điều kiện để Muse tạo video mới")
    for previous in commands:
        previous.status = "failed"
        previous.finished_at = previous.updated_at = now
        previous.lease_hash = previous.lease_expires_at = None
        previous.details = {**(previous.details or {}), "supersededByRegeneration": command_id}
    db.add(command)
    try:
        db.flush()
        if hero_repair is not None:
            await delete_drive_videos(tenant, listing_id, job.artwork_sha256, selected, archive=True)
        else:
            await delete_drive_videos(tenant, listing_id, job.artwork_sha256, selected)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "commandId": command_id, "queued": True,
            **({"quarantinedAspectRatios": list(selected), "videoContentQa": "fail",
                "heroRepairByAspect": receipts["heroRepairByAspect"]} if hero_repair is not None
               else {"deletedAspectRatios": list(selected)}),
            "videoUgc": video_ugc_view(job)}


def apply_hero_repairs(story, job):
    """Append only scoped, typed operator repair constraints to Story Lite."""
    markers = (job.output_receipts or {}).get("heroRepairByAspect") or {}
    revisions = (job.output_receipts or {}).get("videoRegeneration") or {}
    for ratio, marker in markers.items():
        if (ratio not in ASPECTS or not isinstance(marker, dict)
                or marker.get("policyVersion") != "CAM_VIDEO_HERO_CONTRAST_REPAIR_V1"
                or marker.get("artworkSha256") != job.artwork_sha256
                or marker.get("commandId") != revisions.get("commandId")
                or marker.get("requiredColorway") not in ("Black", "Pepper", "Navy")):
            raise ValueError("Invalid hero contrast repair marker")
        plan = story["aspectPlans"][ratio]
        color = marker["requiredColorway"]
        correction = (
            f" Mandatory hero correction for {ratio}: the exact Native Master has light ink. "
            f"Use a dark Comfort Colors C1717 short-sleeve tee in {color}; sufficient clear "
            "light-on-dark contrast is mandatory. Never use a light garment for this artwork. "
            "Edit/reuse the original character and scene reference if available: preserve "
            "character, story, pose style, setting, light and framing. Use natural chest print "
            "size with top ink approximately 150 mm below the collar base, not oversized. "
            "Exact original artwork, text, colors and layout; realistic screen print following "
            "fabric curvature, folds, shadows and wrinkles, no flat sticker overlay. "
            "Keep the same background, locked camera and lighting during motion. "
            "Do not reuse the rejected hero or rejected MP4. Verify the corrected hero once "
            "before render; if a suitable original reference is unavailable, report the limitation."
        )
        if "referenceImagePrompt" not in plan:
            # Existing legacy reference strategy is not used for these explicit
            # Story Lite repairs. A claim pins referenceFirst as normal.
            from .story_lite import reference_image_prompt
            plan["referenceImagePrompt"] = reference_image_prompt(ratio, plan["storyTitle"])
        plan["referenceImagePrompt"] += correction
        plan["prompt"] += (
            f" Preserve the corrected dark {color} C1717 tee, exact readable light-ink print "
            "and fabric integration. Same adult, original story and scene, fixed camera, "
            "no invented background. Reject unreadable contrast, oversized print or flat overlay."
        )
        plan["heroRepair"] = marker
        if ratio == "9:16":
            story["narrativePrompt"] = plan["prompt"]
    return story
