"""One-time operator-approved CAM_VIDEO_RERUN_REQUEST_V1.

Run with the authorized CAM application environment and repository API Python.
Default is a read-only preflight. --apply affects exactly the three requested
listing/aspect/SHA tuples; no claims, renderer invocation, or SMS mutation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.muse_bridge import regeneration, router as muse
from app.modules.muse_bridge.model import CamMuseCommand, CamMuseJob

REQUESTS = (
    {"listing": "4386367106", "ratio": "9:16",
     "old": "d5ab38fab94683c68bf71c2c5f8e795b74b44c1c3e40661aad52afa33d2f24ee",
     "artwork": "561fa6464a36831ee0706e3f3e10b2305e919682879be1bcbe61b66a1a8bdb7e",
     "colorway": "Black", "oversizedFlatPrint": True,
     "correctedLocalSha256": "6ece2f20a8b94dfaa05627294c741d587482942c396e1b39c638c69158fd13e4"},
    {"listing": "4556263261", "ratio": "16:9",
     "old": "05e49bc3040cfdbb43e14b37bd481edb493ced07cea6a010133312364dcb731f",
     "artwork": "567cc68f6f507b019f0197f074986dec6e943e322d492e8920978204ff285c5a",
     "keep": {"9:16": "a0628133c8afa7aa56cde708d48e6d673c537dbcdeee86bdb9ecfed4e68cfcea"},
     "colorway": "Black", "oversizedFlatPrint": False,
     "correctedLocalSha256": "9989b7b6cfe0dc799b360963d8de0d7c3fc6b00425461f6d3fc805be0048e9dc"},
    {"listing": "4357336951", "ratio": "16:9",
     "old": "3b6849f130403b0a994eb041a14d1ef1078c4cddcb843146f549d271c1b2a30c",
     "artwork": "22f2cd4c213eb35bcccf1d3a25278985b2cd64b633d808f5f3c67e898fd49967",
     "keep": {"9:16": "7dda81708fe6dd2cdbf6230f990d5d2ba96517d3d447fcf3f9a2f38561f64cf8"},
     "colorway": "Navy", "oversizedFlatPrint": False,
     "correctedLocalSha256": "5f278a44cf48f410d33534e09ba5bb44e00de2fe7a117e59b375158ecc4964da"},
)
AUDIT = Path("ops/muse-video-contrast-repair-20261127-audit.json")

def snapshot(db, tenant, req):
    lid, ratio = req["listing"], req["ratio"]
    job = db.get(CamMuseJob, (tenant, lid))
    if not job or job.artwork_sha256 != req["artwork"]:
        raise RuntimeError(f"{lid}: artwork or listing changed")
    rec = job.output_receipts or {}
    uploads = rec.get("drive_uploads") or {}
    verified = muse._verified_video_ratios(job)
    for aspect, sha in req.get("keep", {}).items():
        if aspect not in verified or uploads[aspect].get("sha256") != sha:
            raise RuntimeError(f"{lid}: retained sibling changed")
    existing = (rec.get("heroRepairByAspect") or {}).get(ratio)
    if existing:
        history = (rec.get("rejectedVideoHistoryByAspect") or {}).get(ratio, [])
        if (existing.get("rejectedVideoSha256") == req["old"]
                and existing.get("requiredColorway") == req["colorway"]
                and any(v.get("videoSha256") == req["old"] and
                        v.get("videoContentQa") == "fail" for v in history)):
            return {"listing": lid, "ratio": ratio, "alreadyApplied": True,
                    "commandId": existing.get("commandId")}
        raise RuntimeError(f"{lid}: different repair exists")
    if (ratio not in verified or uploads[ratio].get("sha256") != req["old"]
            or rec.get("videoContentQa") != "pending_review"
            or not muse._story_lite_pin(muse._pinned_story_v33(job))):
        raise RuntimeError(f"{lid}: expected pending-review Story Lite SHA missing")
    if job.status == "claimed":
        raise RuntimeError(f"{lid}: job claimed, no changes made")
    commands = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant, CamMuseCommand.listing_key == lid,
        CamMuseCommand.status.in_(("pending", "claimed")))).all()
    for cmd in commands:
        required = (cmd.details or {}).get("requiredVideoAspectRatios")
        if cmd.status == "claimed" or required != [ratio] or cmd.phase != "create_video":
            raise RuntimeError(f"{lid}: existing other work; do not supersede")
    return {"listing": lid, "ratio": ratio, "alreadyApplied": False,
            "oldReceipt": uploads[ratio], "keptReceipts": {
                r: uploads[r] for r in sorted(verified) if r != ratio},
            "originalPin": rec.get("story_contract_v33"),
            "artworkSha256": job.artwork_sha256, "status": job.status}

def persist_audit(tenant, rows):
    document = {"request": "CAM_VIDEO_RERUN_REQUEST_V1",
                "createdAtUtc": datetime.now(timezone.utc).isoformat(),
                "tenant": tenant, "requested": REQUESTS, "before": rows}
    if AUDIT.exists():
        info = AUDIT.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError("Invalid existing private audit file")
        old = json.loads(AUDIT.read_text())
        if old.get("tenant") != tenant or old.get("requested") != list(REQUESTS):
            raise RuntimeError("Existing audit scope mismatch")
        for row in rows:
            if not row["alreadyApplied"]:
                prev = next((r for r in old["before"] if r["listing"] == row["listing"]), None)
                if prev is None or prev.get("oldReceipt") != row["oldReceipt"]:
                    raise RuntimeError("Existing audit receipt mismatch")
        return
    if AUDIT.parent.is_symlink() or not AUDIT.parent.is_dir():
        raise RuntimeError("Private audit parent unavailable")
    fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(document, f, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(AUDIT.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

async def main(apply):
    tenant = os.environ["CAM_MUSE_TENANT_ID"]
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    try:
        with Session(engine) as db:
            rows = [snapshot(db, tenant, req) for req in REQUESTS]
            for row in rows:
                print(json.dumps({"preflight": row["listing"], "ratio": row["ratio"],
                    "alreadyApplied": row["alreadyApplied"], "kept": list(row.get("keptReceipts", {})),
                    "commandId": row.get("commandId")}), flush=True)
        if not apply:
            return
        persist_audit(tenant, rows)
        for req, before in zip(REQUESTS, rows):
            if before["alreadyApplied"]:
                continue
            with Session(engine) as db:
                result = await regeneration.regenerate(db, tenant, req["listing"],
                    {req["ratio"]: req["old"]},
                    hero_repair={k: req[k] for k in (
                        "colorway", "correctedLocalSha256", "oversizedFlatPrint")})
            with Session(engine) as db:
                job = db.get(CamMuseJob, (tenant, req["listing"]))
                rec = job.output_receipts
                for r, receipt in before["keptReceipts"].items():
                    if (rec.get("drive_uploads") or {}).get(r) != receipt:
                        raise RuntimeError("Retained sibling receipt changed; stop")
                failure = rec["rejectedVideoHistoryByAspect"][req["ratio"]][-1]
                assert failure["videoSha256"] == req["old"] and failure["videoContentQa"] == "fail"
                cmd = db.get(CamMuseCommand, (tenant, result["commandId"]))
                assert cmd and (cmd.details or {}).get("requiredVideoAspectRatios") == [req["ratio"]]
                spec = muse._story_candidate_v33(job)
                assert spec and spec["storyOutputContract"] == muse.LITE_OUTPUT
                plan = spec["aspectPlans"][req["ratio"]]
                assert plan["heroRepair"]["requiredColorway"] == req["colorway"]
                print(json.dumps({"applied": req["listing"], "ratio": req["ratio"],
                    "commandId": result["commandId"], "commandStatus": cmd.status,
                    "oldContentQa": "fail", "archivedOriginal": True,
                    "retainedRatios": list(before["keptReceipts"]),
                    "colorway": req["colorway"], "contract": spec["storyOutputContract"]}), flush=True)
    finally:
        engine.dispose()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
