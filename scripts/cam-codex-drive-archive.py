#!/usr/bin/env python3
"""Safely archive legacy Codex generated PNG files into CAM's Managed Google Drive.

DRY RUN by default. --apply creates a private recovery folder, uploads a
bounded number of old files, confirms remote SHA256/MD5/size, and deletes
ONLY unchanged verified originals. It never removes Drive files.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
import stat
import sys
import time

HOMES=(
    Path("/var/lib/creative-asset-manager/codex/generated_images"),
    Path("/var/lib/creative-asset-manager/codex-account-2/generated_images"),
)
RECOVERY_TENANT="cam-system-codex-recovery"
ARCHIVE_FOLDER="CAM Codex Recovery Archive - Internal"
DRIVE_URL="https://www.googleapis.com/drive/v3/files"

def eligible_files(older_hours: int):
    cutoff=time.time()-older_hours*3600
    for home in HOMES:
        if not home.is_dir() or home.is_symlink():
            continue
        for base, dirs, filenames in os.walk(home, followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink()]
            for name in filenames:
                path=Path(base)/name
                try:
                    s=path.lstat()
                    if (path.suffix.lower()==".png" and stat.S_ISREG(s.st_mode)
                        and s.st_nlink==1 and s.st_mtime<cutoff):
                        yield home,path,s
                except OSError:
                    continue

def checksums(path: Path):
    sha=hashlib.sha256()
    md5=hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024), b""):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(),md5.hexdigest()

async def folder_id(client, root_id: str, create: bool):
    params={
        "q": f"'{root_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder' "
             "and appProperties has { key='cam_kind' and value='codex_generated_recovery_v1' }",
        "pageSize":"3","fields":"files(id,name,appProperties)",
        "supportsAllDrives":"true","includeItemsFromAllDrives":"true",
    }
    r=await client.get(DRIVE_URL,params=params)
    r.raise_for_status()
    found=r.json().get("files") or []
    if len(found)>1:
        raise RuntimeError("Multiple recovery folders found; refusing to guess")
    if found:
        return found[0]["id"]
    if not create:
        return None
    r=await client.post(
        DRIVE_URL,params={"supportsAllDrives":"true","fields":"id,name,appProperties"},
        json={"name":ARCHIVE_FOLDER,
              "mimeType":"application/vnd.google-apps.folder",
              "parents":[root_id],
              "appProperties":{"cam_kind":"codex_generated_recovery_v1"}},
    )
    r.raise_for_status()
    return r.json()["id"]

async def verify(client, file_id: str, *, size: int, sha: str, md5: str,
                 tenant: str, asset_id: str, archive_folder: str) -> bool:
    response=await client.get(
        f"{DRIVE_URL}/{file_id}",
        params={"fields":"id,size,md5Checksum,appProperties,parents,mimeType,trashed",
                "supportsAllDrives":"true"},
    )
    if response.status_code!=200:
        return False
    obj=response.json()
    prop=obj.get("appProperties") or {}
    return (obj.get("trashed") is not True
            and str(obj.get("size"))==str(size)
            and str(obj.get("md5Checksum") or "").lower()==md5
            and str(prop.get("cam_content_hash") or "").lower()==sha
            and str(prop.get("cam_tenant_id") or "")==tenant
            and str(prop.get("cam_asset_id") or "")==asset_id
            and archive_folder in (obj.get("parents") or [])
            and obj.get("mimeType")=="image/png")

def remove_exact(path: Path, root: Path, s, sha: str, md5: str, cutoff: float) -> bool:
    try:
        if (root.is_symlink() or not path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            or path.is_symlink()):
            return False
        st=path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1
            or st.st_ino!=s.st_ino or st.st_size!=s.st_size
            or st.st_mtime_ns!=s.st_mtime_ns or st.st_mtime>=cutoff):
            return False
        if checksums(path)!=(sha,md5):
            return False
        again=path.lstat()
        if again.st_ino!=s.st_ino or again.st_mtime_ns!=s.st_mtime_ns:
            return False
        path.unlink()
        return True
    except (OSError,ValueError):
        return False

async def execute(args):
    # The generated_images directory is an unclassified Codex tool cache.
    # It may contain review/draft PNGs; age is not proof of a final.
    # Fail closed for the automatic timer; explicit historical recovery
    # must be a separate operator-approved action.
    if not args.legacy_recovery:
        print("FINAL_ONLY_POLICY: unclassified generated_images cannot be archived.")
        print("Use completed-output Drive records for final assets; "
              "job-scoped drafts must be cleaned without Drive persistence.")
        print("RESULT mode=FINAL_ONLY_UNCLASSIFIED_SKIPPED uploaded=0 deleted=0")
        return
    from dotenv import load_dotenv
    env=Path("/etc/creative-asset-manager/production.env")
    if not env.is_file():
        raise RuntimeError("Production environment not available")
    load_dotenv(env,override=True)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"apps/api"))
    from app.core.config import get_settings
    from app.modules.storage.provider_factory import build_managed_storage_provider
    from app.domain.providers.contracts import StoreAssetInput
    import httpx

    settings=get_settings()
    root_id=str(settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
    if not root_id or not settings.DATABASE_URL or not settings.DATABASE_URL.startswith("postgresql"):
        raise RuntimeError("Missing production Managed Drive/PostgreSQL configuration")
    provider=build_managed_storage_provider(settings)
    if not hasattr(provider,"get_access_token"):
        raise RuntimeError("Production Managed Drive is disconnected")
    token=await provider.get_access_token()
    async with httpx.AsyncClient(
        headers={"Authorization":f"Bearer {token}"},
        timeout=httpx.Timeout(45,connect=12),
    ) as client:
        archive=await folder_id(client,root_id,args.apply)
        if args.apply and not archive:
            raise RuntimeError("Cannot establish recovery folder")
        print("RECOVERY_ARCHIVE=" + ("VERIFIED" if archive else "NOT_YET_CREATED"))
        if archive:
            print(f"RECOVERY_ARCHIVE_URL=https://drive.google.com/drive/folders/{archive}",flush=True)
        cutoff=time.time()-args.min_age_hours*3600
        uploaded=0
        deleted=0
        failed=0
        saved_bytes=0
        inspected=0
        handled=0
        selected=[]
        for home,path,s in eligible_files(args.min_age_hours):
            if len(selected)>=args.max_files:
                break
            selected.append((home,path,s))
        inspected=len(selected)
        semaphore=asyncio.Semaphore(args.concurrency)

        async def handle_one(home,path,s):
            nonlocal uploaded,deleted,failed,saved_bytes,handled
            async with semaphore:
                if not args.apply:
                    handled+=1
                    return
                try:
                    sha,md5=checksums(path)
                    home_id=home.parent.name
                    asset_id=f"codex-source:{home_id}:{sha}"
                    safe_name=f"{home_id}-{sha}.png"

                    async def body(filename=path):
                        with filename.open("rb") as stream:
                            while chunk:=stream.read(1024*1024):
                                yield chunk
                                await asyncio.sleep(0)

                    stored=None
                    for attempt in range(3):
                        try:
                            stored=await provider.store_asset(StoreAssetInput(
                                tenant_id=RECOVERY_TENANT,content_hash=sha,
                                body=body(),asset_id=asset_id,content_type="image/png",
                                size_bytes=s.st_size,filename=safe_name,
                                destination_folder_id=archive,
                            ))
                            break
                        except Exception as exc:
                            if not getattr(exc,"retryable",False) or attempt==2:
                                raise
                            await asyncio.sleep(min(2**attempt * 2, 8))
                    if stored is None or not stored.remote_file_id:
                        raise RuntimeError("Managed storage returned no file ID")
                    verified=await verify(
                        client,stored.remote_file_id,size=s.st_size,sha=sha,md5=md5,
                        tenant=RECOVERY_TENANT,asset_id=asset_id,archive_folder=archive,
                    )
                    if not verified:
                        failed+=1
                        return
                    uploaded+=1
                    if remove_exact(path,home,s,sha,md5,cutoff):
                        deleted+=1
                        saved_bytes+=s.st_blocks*512
                except Exception as exc:
                    failed+=1
                    print(f"FAILED_LOCAL_ASSET reason={type(exc).__name__} (file retained)",flush=True)
                finally:
                    handled+=1
                    if handled%20==0 or handled==inspected:
                        print(f"PROGRESS considered={handled} drive_verified={uploaded} "
                              f"deleted={deleted} errors={failed}",flush=True)

        # Bounded concurrency reduces Drive latency without uncontrolled
        # memory/network usage; each task still gets independent readback proof.
        await asyncio.gather(*(handle_one(*item) for item in selected))
        print(f"RESULT mode={'APPLY' if args.apply else 'DRY_RUN'} considered={inspected} "
              f"drive_verified={uploaded} deleted={deleted} freed_mib={saved_bytes/1048576:.1f} failures={failed}")
        print("Other files, sessions and remote data remain untouched.")

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply",action="store_true")
    ap.add_argument("--legacy-recovery",action="store_true",
                    help="Exceptional operator-led recovery of unclassified historic images; NEVER use in production timer")
    ap.add_argument("--max-files",type=int,default=30)
    ap.add_argument("--min-age-hours",type=int,default=4)
    ap.add_argument("--concurrency",type=int,default=5)
    args=ap.parse_args()
    if not 1<=args.max_files<=500 or not 1<=args.min_age_hours<=720 or not 1<=args.concurrency<=8:
        ap.error("max-files 1..500; min-age-hours 1..720; concurrency 1..8")
    if args.apply and os.geteuid()!=0:
        ap.error("Apply requires root for production managed OAuth credentials")
    asyncio.run(execute(args))
