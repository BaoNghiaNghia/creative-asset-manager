"""CAM_SMS_VIDEO_SHARE_V1: explicit listing-scoped MP4 only."""
from __future__ import annotations
import asyncio
import logging
import re
from dataclasses import dataclass

POLICY = "CAM_SMS_VIDEO_SHARE_V1"
TRANSPORT = "drive_view_link_only"
MARKER = "cam_sms_video_share_permission"
ID = re.compile(r"[A-Za-z0-9_-]{10,128}")
PID = re.compile(r"[A-Za-z0-9_-]{3,128}")
SHA = re.compile(r"[0-9a-f]{64}")
LOCK = asyncio.Lock()
LOG = logging.getLogger(__name__)

class ShareError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status

@dataclass(frozen=True)
class Proof:
    tenant: str
    listing: str
    aspect: str
    artwork: str
    digest: str
    file: str
    folder: str

    @property
    def asset(self):
        slug = "vertical" if self.aspect == "9:16" else "horizontal"
        return f"muse-ugc:{self.listing}:{self.artwork}:{slug}"

    @property
    def url(self):
        return f"https://drive.google.com/file/d/{self.file}/view"

def match(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))

def select_proof(job, tenant, listing, req):
    if (not isinstance(req, dict) or set(req) != {"action","platform","aspect","expected_sha256","expected_file_id","confirm"}
        or req.get("confirm") is not True or req.get("action") not in ("prepare","revoke")
        or req.get("platform") not in ("TiktokShop","Etsy") or req.get("aspect") not in ("9:16","16:9")
        or (req["platform"] == "TiktokShop" and req["aspect"] != "9:16")
        or not isinstance(listing,str) or not re.fullmatch(r"[0-9]{5,20}",listing)):
        raise ShareError("INVALID_OR_UNCONFIRMED_SMS_VIDEO_SHARE",422)
    if job is None or job.tenant_id != tenant or job.listing_key != listing:
        raise ShareError("SMS_VIDEO_NOT_FOUND",404)
    receipts = job.output_receipts
    uploads = receipts.get("drive_uploads") if isinstance(receipts,dict) else None
    row = uploads.get(req["aspect"]) if isinstance(uploads,dict) else None
    if (not isinstance(row,dict) or row.get("storageVerified") is not True
        or row.get("storage_provider") != "google_drive_managed" or row.get("contentType") not in (None,"video/mp4")):
        raise ShareError("SMS_VIDEO_STORAGE_UNVERIFIED")
    f,d,s = row.get("drive_file_id"),row.get("drive_folder_id"),row.get("sha256")
    if not (match(ID,f) and match(ID,d) and f != d and match(SHA,s) and match(SHA,job.artwork_sha256)):
        raise ShareError("SMS_VIDEO_PROOF_INVALID")
    if req["expected_sha256"] != s or req["expected_file_id"] != f:
        raise ShareError("SMS_VIDEO_CHANGED")
    return Proof(tenant,listing,req["aspect"],job.artwork_sha256,s,f,d)

def metadata_check(data,p):
    props = data.get("appProperties") or {}
    if (data.get("id") != p.file or data.get("trashed") is not False or data.get("mimeType") != "video/mp4"
        or data.get("parents") != [p.folder] or not isinstance(props,dict)
        or props.get("cam_tenant_id") != p.tenant or props.get("cam_asset_id") != p.asset
        or props.get("cam_content_hash") != p.digest):
        raise ShareError("SMS_VIDEO_DRIVE_PROVENANCE_MISMATCH")
    if not isinstance(data.get("permissions"),list) or data.get("capabilities",{}).get("canShare") is not True:
        raise ShareError("SMS_VIDEO_SHARE_PERMISSION_UNAVAILABLE",403)
    public = [v for v in data["permissions"] if v.get("type") == "anyone"]
    if len(public) > 1 or (public and (public[0].get("role") != "reader"
        or public[0].get("allowFileDiscovery") is not False or not match(PID,public[0].get("id")))):
        raise ShareError("SMS_VIDEO_EXISTING_PUBLIC_PERMISSION_CONFLICT")
    marker = props.get(MARKER)
    if marker is not None and not match(PID,marker):
        raise ShareError("SMS_VIDEO_SHARE_MARKER_INVALID")
    return (public[0]["id"] if public else None), marker


async def handoff(p, action, drive, probe, *, inherited_root=False):
    """Only change the selected file; preserve pre-existing grants."""
    async with LOCK:
        existing, marker = metadata_check(await drive.metadata(p.file), p)
        if inherited_root and action == "revoke":
            raise ShareError("SMS_VIDEO_ROOT_VIEWER_INHERITED")
        if inherited_root and not existing:
            raise ShareError("SMS_VIDEO_ROOT_VIEWER_NOT_INHERITED")
        if action == "revoke":
            if not existing:
                if marker:
                    await drive.marker(p.file, None)
                return {"policy": POLICY, "state": "restricted", "changed": False}
            if existing != marker:
                raise ShareError("SMS_VIDEO_PERMISSION_NOT_CREATED_BY_CAM")
            await drive.revoke(p.file, existing)
            if metadata_check(await drive.metadata(p.file), p)[0]:
                raise ShareError("SMS_VIDEO_REVOKE_READBACK_FAILED", 502)
            await drive.marker(p.file, None)
            LOG.info("sms_video_share revoked tenant=%s listing=%s aspect=%s", p.tenant, p.listing, p.aspect)
            return {"policy": POLICY, "state": "restricted", "changed": True}
        if action != "prepare":
            raise ShareError("INVALID_SMS_VIDEO_ACTION", 422)
        if marker and marker != existing:
            raise ShareError("SMS_VIDEO_SHARE_MARKER_CONFLICT")
        created = None
        grant_attempted = False
        try:
            if not existing:
                grant_attempted = True
                created = await drive.grant(p.file)
                if not match(PID, created):
                    created = None
                    raise ShareError("SMS_VIDEO_GRANT_RECEIPT_INVALID", 502)
                await drive.marker(p.file, created)
            fresh = await drive.metadata(p.file)
            checked, checked_marker = metadata_check(fresh, p)
            if checked != (created or existing) or (created and checked_marker != created):
                raise ShareError("SMS_VIDEO_GRANT_READBACK_FAILED", 502)
            view_url = p.url
            resource_key = fresh.get("resourceKey")
            if resource_key:
                if not match(ID, resource_key):
                    raise ShareError("SMS_VIDEO_RESOURCE_KEY_INVALID", 502)
                view_url += "?resourcekey=" + resource_key
            if not await probe(view_url):
                raise ShareError("SMS_VIDEO_VIEW_PAGE_UNAVAILABLE", 502)
        except (Exception, asyncio.CancelledError):
            if created:
                try:
                    await drive.revoke(p.file, created)
                    await drive.marker(p.file, None)
                    if metadata_check(await drive.metadata(p.file), p)[0]:
                        raise RuntimeError("permission remains")
                except (Exception, asyncio.CancelledError):
                    LOG.error("sms_video_share rollback failed tenant=%s listing=%s aspect=%s", p.tenant, p.listing, p.aspect)
                    raise ShareError("SMS_VIDEO_SHARE_ROLLBACK_REVIEW_REQUIRED", 502) from None
            elif grant_attempted:
                LOG.error("sms_video_share grant outcome unknown tenant=%s listing=%s aspect=%s", p.tenant, p.listing, p.aspect)
                raise ShareError("SMS_VIDEO_SHARE_GRANT_REVIEW_REQUIRED", 502) from None
            raise
        LOG.info("sms_video_share prepared tenant=%s listing=%s aspect=%s new=%s", p.tenant, p.listing, p.aspect, bool(created))
        # Only idea-level link fields: target video_urls can trigger SMS import.
        return {"policy": POLICY, "transport": TRANSPORT, "state": "viewer_link_verified",
                "changed": bool(created), "view_url": view_url,
                "fields": [{"name": f"UGC Video {p.aspect} (Drive link)", "value": view_url}],
                "sha256": p.digest, "aspect": p.aspect,
                "cam_managed_permission": bool(created or marker),
                "viewer_permission_verified": True, "view_page_reachable": True,
                "sms_upload_requested": False}


class Drive:
    API = "https://www.googleapis.com/drive/v3/files"

    def __init__(self, client):
        self.client = client

    async def request(self, method, f, suffix="", **kw):
        if not match(ID, f):
            raise ShareError("SMS_VIDEO_ID_INVALID")
        r = await self.client.request(method, f"{self.API}/{f}{suffix}", **kw)
        if r.status_code not in (200, 201, 204):
            raise ShareError("SMS_VIDEO_DRIVE_API_FAILED", 502)
        return r.json() if r.content else {}

    async def metadata(self, f):
        return await self.request("GET", f, params={"supportsAllDrives": "true",
            "fields": "id,trashed,mimeType,parents,resourceKey,appProperties,capabilities(canShare),permissions(id,type,role,allowFileDiscovery)"})

    async def grant(self, f):
        data = await self.request("POST", f, "/permissions",
            params={"supportsAllDrives": "true", "fields": "id"},
            json={"type": "anyone", "role": "reader", "allowFileDiscovery": False})
        return data.get("id")

    async def revoke(self, f, permission):
        if not match(PID, permission):
            raise ShareError("SMS_VIDEO_PERMISSION_ID_INVALID")
        await self.request("DELETE", f, f"/permissions/{permission}", params={"supportsAllDrives": "true"})

    async def marker(self, f, permission):
        await self.request("PATCH", f, params={"supportsAllDrives": "true", "fields": "id"},
                           json={"appProperties": {MARKER: permission}})


async def probe_view_page(client, view_url):
    """Anonymous HEAD only, never fetch media bytes or follow redirects.
    A reachable HTML page plus verified ACL does not prove playback in SMS.
    """
    if not isinstance(view_url, str) or not re.fullmatch(
            r"https://drive\.google\.com/file/d/[A-Za-z0-9_-]{10,128}/view(?:\?resourcekey=[A-Za-z0-9_-]{10,128})?",
            view_url):
        return False
    async with client.stream("HEAD", view_url, headers={"Accept": "text/html"},
                             follow_redirects=False) as r:
        return (r.status_code == 200 and
                r.headers.get("content-type", "").split(";")[0].strip().lower() == "text/html")


async def live(p, action):
    import httpx
    from .imagegen_drive import provider_and_folder, verify_private_drive_destination
    try:
        provider, root = provider_and_folder()
        root_sharing = await verify_private_drive_destination(provider, root)
        token = await provider.get_access_token()
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as auth:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5),
                follow_redirects=False, trust_env=False) as anon:
                return await handoff(p, action, Drive(auth), lambda url: probe_view_page(anon, url),
                                     inherited_root=root_sharing == "link_viewer")
    except ShareError:
        raise
    except Exception:
        raise ShareError("SMS_VIDEO_SHARE_PROVIDER_UNAVAILABLE", 502) from None
