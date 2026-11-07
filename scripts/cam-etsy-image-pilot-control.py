#!/usr/bin/env python3
"""Fail-closed activation for one Etsy extraction pilot claim/day.

Only CAM production server may run it. No Etsy data changes or new jobs are
created here. The Windows Image Agent remains disabled until separately armed.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import stat
import tempfile

from dotenv import load_dotenv

ENV = Path("/etc/creative-asset-manager/production.env")
KEY = "CAM_ETSY_EXTRACTION_WORKER_ENABLED"


def set_flag(on: bool) -> None:
    old = ENV.read_text(encoding="utf-8")
    lines = old.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if re.match(r"^\s*CAM_ETSY_EXTRACTION_WORKER_ENABLED\s*=", line)]
    if len(matches) > 1:
        raise RuntimeError("duplicate_extraction_flag")
    replacement = KEY + "=" + ("1" if on else "0") + "\n"
    if matches:
        lines[matches[0]] = replacement
    else:
        lines.append(("\n" if old and not old.endswith("\n") else "") + replacement)
    updated = "".join(lines)
    metadata = ENV.stat()
    # Same ownership/mode as the original protected environment, atomic rename.
    fd, temp = tempfile.mkstemp(prefix=".image-pilot-", dir=str(ENV.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(updated)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp, stat.S_IMODE(metadata.st_mode))
        os.chown(temp, metadata.st_uid, metadata.st_gid)
        os.replace(temp, ENV)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["status", "arm", "disarm"])
    parser.add_argument("--agent-id", default="")
    args = parser.parse_args()
    if os.name != "posix" or not ENV.is_file():
        raise SystemExit("production_host_required")
    load_dotenv(ENV, override=False)
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("production_database_required")
    from sqlalchemy import select, func
    from app.core.database import SessionLocal
    from app.modules.etsy_spy.model import EtsySpyAgent, EtsySpyPicked
    from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX
    from app.modules.etsy_spy.image_agent import storage_capacity, daily_cap, claimed_today
    from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
    from app.modules.processing.model import ProcessingJobModel
    with SessionLocal() as db:
        tenant = "boostgame"
        pending = int(db.scalar(select(func.count()).select_from(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant,
            ProcessingJobModel.job_type == EXTRACTION_JOB_TYPE,
            ProcessingJobModel.status.in_(("pending", "retry", "processing")),
            ProcessingJobModel.cancellation_requested.is_(False),
        )) or 0)
        picked = int(db.scalar(select(func.count()).select_from(EtsySpyPicked).where(
            EtsySpyPicked.tenant_id == tenant,
        )) or 0)
        active = db.scalars(select(EtsySpyAgent).where(
            EtsySpyAgent.tenant_id == tenant,
            EtsySpyAgent.name.startswith(IMAGE_AGENT_NAME_PREFIX),
            EtsySpyAgent.active.is_(True),
        )).all()
        status = {
            "storage_ready": storage_capacity()["ready"],
            "daily_cap": daily_cap(),
            "claimed_today": claimed_today(db, tenant),
            "pending_jobs": pending,
            "picked_listings": picked,
            "paired_image_agents": len(active),
            "worker_enabled": os.getenv(KEY) == "1",
        }
        if args.mode == "status":
            print(json.dumps(status))
            return
        if args.mode == "arm":
            device = next((a for a in active if a.id == args.agent_id), None)
            last_seen = device.last_seen_at if device else None
            if last_seen is not None and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            recent = bool(last_seen and
                          last_seen > datetime.now(timezone.utc)-timedelta(minutes=30))
            if (device is None or not recent or not status["storage_ready"] or
                status["daily_cap"] != 1 or status["claimed_today"] != 0 or
                status["pending_jobs"] != 0):
                raise SystemExit("pilot_preflight_not_ready; no changes")
            set_flag(True)
            print(json.dumps({"pilot_flag":"armed","max_daily_claims":1,
                              "requires_api_restart":True}))
        else:
            set_flag(False)
            print(json.dumps({"pilot_flag":"disarmed","requires_api_restart":True}))


if __name__ == "__main__":
    main()
