"""Explicit operator-only export of already shared, SHA-verified original PNGs.

Never grant public permission, proxy private media, change SMS, or infer color.
"""
import hashlib
import re
from urllib.parse import urljoin, urlsplit
import httpx
from .sms_mockup_sync import plan_chunks

ID = re.compile(r"[A-Za-z0-9_-]{10,128}\Z")
MAX_PNG = 32 * 1024 * 1024
MAX_PACK = 160 * 1024 * 1024

def _safe_host(url):
    p=urlsplit(url)
    return (p.scheme=="https" and not p.username and not p.password
            and p.port in (None,443) and not p.fragment
            and (p.hostname in ("drive.google.com","drive.usercontent.google.com")
                 or bool(p.hostname and p.hostname.endswith(".googleusercontent.com"))))

async def verify_public_png(client,url,sha,limit=MAX_PNG):
    """Streaming anonymous GET, bounded and exact SHA; no thumbnail substitute."""
    digest=hashlib.sha256()
    size=0
    prefix=b""
    for _ in range(4):
        if not _safe_host(url):
            raise ValueError("unsafe_image_redirect")
        async with client.stream("GET",url,follow_redirects=False) as response:
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get("location")
                if not location:
                    raise ValueError("invalid_image_redirect")
                url=urljoin(url,location)
                continue
            if (response.status_code!=200 or response.headers.get("content-type","").split(";")[0].lower()!="image/png"):
                raise ValueError("original_png_not_public")
            async for chunk in response.aiter_bytes():
                size+=len(chunk)
                if size>limit:
                    raise ValueError("original_png_size_limit")
                if len(prefix)<8:
                    prefix=(prefix+chunk)[:8]
                digest.update(chunk)
            if prefix!=b"\x89PNG\r\n\x1a\n" or digest.hexdigest()!=sha:
                raise ValueError("original_png_sha_mismatch")
            return size
    raise ValueError("image_redirect_limit")

async def prepare(pack,rows,choices,*,target_id,shop):
    # Reuse the already-approved root sharing policy. A restricted root is a
    # blocker, never an invitation to grant permissions.
    from .imagegen_root_sharing import run_root_sharing
    if not pack.get("ready_for_mockup_review"):
        raise ValueError("pack_not_ready")
    if (await run_root_sharing(apply=False)).get("state")!="link_viewer":
        raise ValueError("approved_root_not_link_viewer")
    expected={p["slot"]:p for p in pack["photos"]}
    if len(rows)!=13 or {r.slot for r in rows}!=set(expected):
        raise ValueError("pack_changed")
    evidence={}
    total=0
    async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=5),trust_env=False,
                                 follow_redirects=False,headers={"Cache-Control":"no-store"}) as client:
        for r in sorted(rows,key=lambda x:x.slot):
            if (r.output_sha256!=expected[r.slot]["output_sha256"]
                    or not ID.fullmatch(r.drive_file_id or "")
                    or r.drive_file_id==r.drive_thumbnail_id):
                raise ValueError("original_identity_changed")
            url="https://drive.google.com/uc?export=download&id="+r.drive_file_id
            total+=await verify_public_png(client,url,r.output_sha256,min(MAX_PNG,MAX_PACK-total))
            evidence[r.slot]={"url":url,"output_sha256":r.output_sha256,"verified_original":True}
    result=plan_chunks(pack,evidence,choices,target_id=target_id,shop=shop)
    return {**result,"original_bytes_verified":total,
            "target_color_choices_supplied_by_operator":True,
            "live_sms_verified_by_cam":False,"no_sharing_changes":True}
