#!/usr/bin/env python3
"""Compare Codex generated_images with CAM completed jobs and Managed Drive.
No deletion in this tool. Secrets, filenames, and user content are not printed.
Drive verification requires exact local SHA256 and remote MD5, not a job state.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import defaultdict
import hashlib
import os
from pathlib import Path
import stat
import sys
import time

ROOTS = (
    Path("/var/lib/creative-asset-manager/codex/generated_images"),
    Path("/var/lib/creative-asset-manager/codex-account-2/generated_images"),
)

def report_local() -> dict[str, list[tuple[Path,int,str,str,int,int]]]:
    records = defaultdict(list)
    counts = defaultdict(lambda: [0,0])
    for root in ROOTS:
        if not root.is_dir() or root.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not (Path(dirpath)/d).is_symlink()]
            for name in filenames:
                path = Path(dirpath)/name
                try:
                    s = path.lstat()
                    if not stat.S_ISREG(s.st_mode) or path.suffix.lower() != ".png":
                        continue
                    sha = hashlib.sha256()
                    md5 = hashlib.md5(usedforsecurity=False)
                    with path.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024*1024), b""):
                            sha.update(chunk)
                            md5.update(chunk)
                    after = path.lstat()
                    if (after.st_ino, after.st_size, after.st_mtime_ns) != (s.st_ino, s.st_size, s.st_mtime_ns):
                        continue
                    records[sha.hexdigest()].append((path,s.st_size,md5.hexdigest(),str(root),s.st_ino,s.st_mtime_ns))
                    counts[str(root)][0] += 1
                    counts[str(root)][1] += s.st_blocks*512
                except OSError:
                    continue
    for root, (count,size) in counts.items():
        print(f"LOCAL {Path(root).parent.name}: files={count} allocated_mib={size/1048576:.1f}")
    return records

def db_hashes():
    # Connect to production using the same env/OAuth connection as CAM, never
    # via the user's personal Drive account.
    from dotenv import load_dotenv
    env = Path("/etc/creative-asset-manager/production.env")
    if not env.is_file():
        raise RuntimeError("Production configuration unavailable")
    load_dotenv(env, override=True)
    code_root = str(Path(__file__).resolve().parents[1] / "apps/api")
    sys.path.insert(0, code_root)
    from app.core.config import get_settings
    from app.core.database import create_database_engine
    from sqlalchemy import text
    settings = get_settings()
    if not settings.DATABASE_URL or not settings.DATABASE_URL.startswith("postgresql"):
        raise RuntimeError("Refusing non-production/non-Postgres database")
    engine = create_database_engine(settings)
    # Every candidate must be tied to a completed job. Stage 1 output
    # versions do not store SHA256; their Drive appProperties and MD5 will
    # independently prove identity before any image can be released.
    record_by_id = defaultdict(set)
    seen = defaultdict(int)
    statements = {
        "rrugc_generation_attempts": """
        SELECT tenant_id, output_content_hash, output_remote_file_id, output_size_bytes
        FROM rrugc_generation_attempts
        WHERE status='completed' AND output_content_hash IS NOT NULL
          AND output_remote_file_id IS NOT NULL
        """,
        "rrugc_stage2_jobs": """
        SELECT tenant_id, output_content_hash, output_remote_file_id, output_size_bytes
        FROM rrugc_stage2_jobs WHERE status='completed'
          AND output_remote_file_id IS NOT NULL
        """,
        "rrugc_colorway_jobs": """
        SELECT tenant_id, NULL, output_remote_file_id, output_size_bytes
        FROM rrugc_colorway_jobs WHERE status='completed'
          AND output_remote_file_id IS NOT NULL
        """,
        "rrugc_keyword_image_jobs": """
        SELECT tenant_id, NULL, output_remote_file_id, output_size_bytes
        FROM rrugc_keyword_image_jobs WHERE status='completed'
          AND output_remote_file_id IS NOT NULL
        """,
        "rrugc_image_output_versions": """
        SELECT v.tenant_id, NULL, v.remote_file_id, v.size_bytes
        FROM rrugc_image_output_versions v
        JOIN rrugc_keyword_image_jobs j
          ON j.tenant_id=v.tenant_id AND j.id=v.job_id
        WHERE v.stage='stage1' AND j.status='completed'
          AND v.remote_file_id IS NOT NULL
        """,
    }
    with engine.connect() as conn:
        for label, sql in statements.items():
            for tenant,digest,file_id,size in conn.execute(text(sql)):
                if not tenant or not file_id:
                    continue
                normalized = digest.lower() if isinstance(digest,str) and len(digest)==64 else ""
                record_by_id[str(file_id)].add((str(tenant), normalized, int(size) if size is not None else -1))
                seen[label]+=1
    engine.dispose()
    for label,count in seen.items():
        print(f"COMPLETED_CAM_RECORDS {label}: {count}")
    print(f"UNIQUE_COMPLETED_REMOTE_FILES={len(record_by_id)}")
    return settings,record_by_id

async def remote_verify(settings, remote_ids, records, limit):
    import httpx
    from app.modules.storage.provider_factory import build_managed_storage_provider
    provider=build_managed_storage_provider(settings)
    if not hasattr(provider,"get_access_token"):
        raise RuntimeError("Managed Drive credentials not configured; aborting verification")
    token=await provider.get_access_token()
    sem=asyncio.Semaphore(4)
    matched=[]
    errors=0
    checked=0
    # Remote metadata must match a candidate local size or a known hash;
    # metadata with absent size is checked conservatively by the Drive API.
    by_size=defaultdict(list)
    for digest,files in records.items():
        for item in files:
            by_size[item[1]].append((digest,item))
    candidates=[]
    for remote_id,entries in remote_ids.items():
        if any((size<0 or size in by_size or (digest and digest in records))
               for tenant,digest,size in entries):
            candidates.append((remote_id,entries))
    print(f"REMOTE_SIZE_OR_HASH_CANDIDATES={len(candidates)}")
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(20,connect=9),
    ) as client:
        async def check(remote_id,entries):
            nonlocal errors,checked
            async with sem:
                for attempt in range(3):
                    try:
                        response=await client.get(
                            f"https://www.googleapis.com/drive/v3/files/{remote_id}",
                            params={"fields":"id,size,md5Checksum,appProperties,trashed,mimeType",
                                    "supportsAllDrives":"true"},
                        )
                        if response.status_code in (429,500,502,503,504) and attempt<2:
                            await asyncio.sleep(0.8*(attempt+1))
                            continue
                        checked+=1
                        if response.status_code!=200:
                            errors+=1
                            return
                        info=response.json()
                        props=info.get("appProperties") or {}
                        if (info.get("trashed") or not info.get("md5Checksum")
                                or info.get("mimeType")!="image/png"):
                            return
                        remote_size=int(info.get("size") or -1)
                        candidates_for_size=by_size.get(remote_size,())
                        for tenant,known_sha,expected_size in entries:
                            if expected_size>=0 and expected_size!=remote_size:
                                continue
                            if str(props.get("cam_tenant_id") or "")!=tenant:
                                continue
                            drive_sha=str(props.get("cam_content_hash") or "").lower()
                            if not drive_sha or known_sha and known_sha!=drive_sha:
                                continue
                            for digest,item in candidates_for_size:
                                if (digest==drive_sha
                                    and info["md5Checksum"].lower()==item[2]):
                                    matched.append((digest,remote_id,item))
                        return
                    except (httpx.HTTPError,ValueError):
                        errors+=1
                        return
        candidates.sort(key=lambda x:x[0])
        if len(candidates)>limit:
            print(f"REMOTE_LIMIT={limit}; excess files remain unverified and untouched")
        await asyncio.gather(*(check(*entry) for entry in candidates[:limit]))
    return matched,checked,errors

def delete_verified(matched, *, min_age_hours):
    # Only full remote proof (tenant+job+sha256+MD5+size) is accepted.
    # Protect recent files and live jobs: images newer than the grace window
    # remain for retry/recovery even if Drive already contains a copy.
    removed=0
    freed=0
    recent=0
    vanished=0
    unique={}
    for digest, remote_id, item in matched:
        unique[str(item[0])] = (digest,item)
    for filename,(digest,item) in unique.items():
        path,size,md5,root,inode,mtime=item
        try:
            r=Path(root).resolve(strict=True)
            current=path.lstat()
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink!=1
                or current.st_ino!=inode or current.st_size!=size
                or current.st_mtime_ns!=mtime or path.is_symlink()
                or not path.resolve(strict=True).is_relative_to(r)):
                vanished+=1
                continue
            if time.time() - current.st_mtime < min_age_hours*3600:
                recent+=1
                continue
            check_sha=hashlib.sha256()
            check_md5=hashlib.md5(usedforsecurity=False)
            with path.open("rb") as stream:
                for block in iter(lambda:stream.read(1024*1024),b""):
                    check_sha.update(block)
                    check_md5.update(block)
            current2=path.lstat()
            if (check_sha.hexdigest()!=digest or check_md5.hexdigest()!=md5
                or current2.st_ino!=inode or current2.st_mtime_ns!=mtime):
                vanished+=1
                continue
            path.unlink()
            removed+=1
            freed+=current.st_blocks*512
        except (OSError,ValueError):
            vanished+=1
    print(f"DELETED_VERIFIED_FILES={removed} freed_mib={freed/1048576:.1f}")
    print(f"RETAINED_RECENT_VERIFIED={recent} RETAINED_CHANGED_FILES={vanished}")
    return removed

async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-drive-requests",type=int,default=5000)
    parser.add_argument("--apply",action="store_true",
                        help="Only unlink remote-verified identical files after grace window")
    parser.add_argument("--min-age-hours",type=int,default=4)
    args=parser.parse_args()
    if not 0<=args.max_drive_requests<=10000:
        parser.error("max-drive-requests must be 0-10000")
    if not 1<=args.min_age_hours<=720:
        parser.error("min-age-hours must be 1-720")
    if args.apply and os.geteuid()!=0:
        parser.error("Apply requires root")
    recs=report_local()
    settings,remote_ids=db_hashes()
    matched,checked,errors=await remote_verify(settings,remote_ids,recs,args.max_drive_requests)
    found={str(item[0]):(digest,remote_id,item) for digest,remote_id,item in matched}
    bytes_verified=sum(item[2][1] for item in found.values())
    print(f"DRIVE_VERIFIED_FILES={len(found)} bytes={bytes_verified} drive_requests={checked} errors={errors}")
    all_files=sum(len(items) for items in recs.values())
    print(f"UNVERIFIED_LOCAL_FILES={max(0,all_files-len(found))}")
    if args.apply:
        delete_verified(list(found.values()),min_age_hours=args.min_age_hours)
    else:
        print("DRY-RUN: no generated PNG files or Drive files were changed.")
    print("Any unmatched/local-only images stay protected. This tool never deletes Drive files.")

if __name__=="__main__":
    asyncio.run(main())
