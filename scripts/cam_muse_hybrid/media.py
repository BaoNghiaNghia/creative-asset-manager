"""Technical MP4 checks and ACK-protected local media staging.

The existing authorized backend downloader must prove the file is from the
exact task; arbitrary external URLs are not fetched here. The existing CAM
runner remains responsible for visual/content QA and Drive receipt. There is
no TTL eviction for unacknowledged files, and no transcoding or padding.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import subprocess

from core import HybridBlocked, SHA, digest

# Match CAM 1.4.23 video_storage.validate_mp4, rather than invent a tighter
# client-side codec, geometry, or duration policy. Additional duration bounds
# come from the exact claimed contract (Story Lite: 5.0-12.5).
MIN_VIDEO_BYTES=2048
MAX_VIDEO_BYTES=40*1024*1024
SPOOL_CAP_BYTES=2*MAX_VIDEO_BYTES


def validate_mp4(data,ratio,duration_bounds=None,*,ffprobe="ffprobe"):
    if (not isinstance(data,bytes) or not MIN_VIDEO_BYTES<=len(data)<=MAX_VIDEO_BYTES
            or ratio not in ("9:16","16:9") or data[4:8]!=b"ftyp"
            or b"moov" not in data or b"mdat" not in data):
        raise HybridBlocked("INVALID_OR_INCOMPLETE_MP4")
    try:
        checked=subprocess.run([ffprobe,"-v","error","-protocol_whitelist","pipe",
            "-show_entries","stream=codec_name,codec_type,width,height,duration",
            "-show_entries","format=duration","-of","json","-i","pipe:0"],
            input=data,capture_output=True,timeout=15,check=True)
        probe=json.loads(checked.stdout[:60000])
        visual=next(v for v in probe.get("streams",[]) if v.get("codec_type")=="video")
        width,height=int(visual["width"]),int(visual["height"])
        duration=float(probe.get("format",{}).get("duration") or visual.get("duration") or 0)
    except (OSError,ValueError,TypeError,KeyError,StopIteration,subprocess.SubprocessError):
        raise HybridBlocked("MP4_FFPROBE_FAILED") from None
    target=9/16 if ratio=="9:16" else 16/9
    if (visual.get("codec_name")!="h264"
            or not 256<=width<=4096 or not 256<=height<=4096
            or abs(width/height-target)>0.07
            or not math.isfinite(duration) or not 5<=duration<=60):
        raise HybridBlocked("MP4_CODEC_ASPECT_DURATION_INVALID")
    seconds=round(duration,2)
    if duration_bounds is not None:
        try:
            low,high=duration_bounds
            if (not all(type(v) in (int,float) and math.isfinite(v) for v in (low,high))
                    or not 0<low<=high):
                raise ValueError()
        except (ValueError,TypeError):
            raise HybridBlocked("INVALID_CLAIM_DURATION_BOUNDS") from None
        if not low<=seconds<=high:
            raise HybridBlocked("MP4_OUTSIDE_CLAIM_DURATION")
    return {"sha256":digest(data),"bytes":len(data),"width":width,"height":height,
            "durationSeconds":seconds,"aspectRatio":ratio,"codec":"h264"}


class MediaSpool:
    """Private technical files alongside the owner's durable journal.

    Only one render is active. A file with no verified receipt is never
    evicted automatically, even if the disk is full. This is not a new CAM
    object store. Keep this directory private and do not expose it over HTTP.
    """
    def __init__(self,journal,*,ffprobe="ffprobe"):
        journal._check_open()
        self.journal=journal
        self.ffprobe=ffprobe
        self.root=journal.root/"media"
        self.root.mkdir(mode=0o700,exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise HybridBlocked("UNSAFE_MEDIA_SPOOL")

    def _path(self,key):
        if not isinstance(key,str) or not SHA.fullmatch(key):
            raise HybridBlocked("INVALID_MEDIA_KEY")
        p=self.root/(key+".mp4")
        if p.is_symlink():
            raise HybridBlocked("UNSAFE_MEDIA_FILE")
        return p

    def _read(self,p):
        if not p.is_file() or not MIN_VIDEO_BYTES<=p.stat().st_size<=MAX_VIDEO_BYTES:
            raise HybridBlocked("MEDIA_FILE_UNAVAILABLE")
        data=p.read_bytes()
        if not MIN_VIDEO_BYTES<=len(data)<=MAX_VIDEO_BYTES:
            raise HybridBlocked("MEDIA_FILE_UNAVAILABLE")
        return data

    def stage(self,key,task_id,data,duration_bounds,verify_task_media):
        with self.journal.mutex:
            self.journal._check_open()
            record=self.journal.records.get(key)
            if (record is None or record["state"]!="result_ready"
                    or record.get("task_id")!=task_id):
                raise HybridBlocked("TASK_MEDIA_NOT_CURRENT")
            if not isinstance(data,bytes) or len(data)>MAX_VIDEO_BYTES:
                raise HybridBlocked("MEDIA_SIZE_INVALID")
            sha=digest(data)
            # Exact task/result lineage must be established by the owning
            # gateway integration, not by MIME, arbitrary URL or prompt text.
            if verify_task_media(key,task_id,sha) is not True:
                raise HybridBlocked("TASK_MEDIA_BINDING_UNVERIFIED")
            metadata=validate_mp4(data,record["intent"]["identity"]["ratio"],
                                  duration_bounds,ffprobe=self.ffprobe)
            path=self._path(key)
            if path.exists():
                if digest(self._read(path))!=sha:
                    raise HybridBlocked("MEDIA_FILE_CONFLICT_RECONCILE")
                return metadata
            used=sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
            if used+len(data)>SPOOL_CAP_BYTES:
                raise HybridBlocked("MEDIA_SPOOL_FULL_PRESERVE_UNACKNOWLEDGED")
            temp=self.root/(".stage-"+secrets.token_hex(8)+".tmp")
            try:
                fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,"wb") as f:
                    f.write(data); f.flush(); os.fsync(f.fileno())
                os.replace(temp,path)
                if os.name!="nt":
                    fd=os.open(self.root,os.O_RDONLY)
                    try: os.fsync(fd)
                    finally: os.close(fd)
            finally:
                temp.unlink(missing_ok=True)
            return metadata

    def read_bound_media(self,key,expected_sha):
        with self.journal.mutex:
            self.journal._check_open()
            record=self.journal.records.get(key)
            if not record or record.get("media_sha256")!=expected_sha or record["state"] not in ("awaiting_receipt","verified"):
                raise HybridBlocked("MEDIA_NOT_QA_BOUND")
            data=self._read(self._path(key))
            if digest(data)!=expected_sha:
                raise HybridBlocked("MEDIA_SHA_CHANGED")
            return data

    def cleanup_verified(self,key):
        with self.journal.mutex:
            self.journal._check_open()
            record=self.journal.records.get(key)
            if not record or record["state"]!="verified" or not record.get("receipt_id"):
                raise HybridBlocked("CAM_ACK_REQUIRED_FOR_CLEANUP")
            p=self._path(key)
            if not p.exists():
                return False
            if digest(self._read(p))!=record.get("media_sha256"):
                raise HybridBlocked("MEDIA_SHA_CHANGED")
            p.unlink()
            return True
