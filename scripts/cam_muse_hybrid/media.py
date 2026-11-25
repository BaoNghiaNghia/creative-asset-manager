"""Native MP4 pass-through and ACK-protected local media staging.

The existing authorized backend downloader must prove the file is from the
exact task; arbitrary external URLs are not fetched here. The existing CAM
runner remains responsible for visual/content QA and Drive receipt. There is
no TTL eviction for unacknowledged files, and no transcoding or padding.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets

from core import HybridBlocked, SHA, digest
from native_mp4 import inspect_native_mp4, InvalidNativeMP4, MIN_VIDEO_BYTES, MAX_VIDEO_BYTES

SPOOL_CAP_BYTES=2*MAX_VIDEO_BYTES


def validate_native_mp4(data,ratio):
    """Check only the native container and existing header aspect, no decoder."""
    if ratio not in ("9:16","16:9"):
        raise HybridBlocked("INVALID_ASPECT")
    try:
        metadata=inspect_native_mp4(data)
    except InvalidNativeMP4:
        raise HybridBlocked("INVALID_OR_INCOMPLETE_MP4") from None
    width,height=metadata.get("width"),metadata.get("height")
    target=9/16 if ratio=="9:16" else 16/9
    if width and height and abs(width/height-target)>0.07:
        raise HybridBlocked("NATIVE_MP4_ASPECT_MISMATCH")
    return {**metadata,"sha256":digest(data),"bytes":len(data),
            "aspectRatio":ratio,"nativeBytesPreserved":True}


class MediaSpool:
    """Private technical files alongside the owner's durable journal.

    Only one render is active. A file with no verified receipt is never
    evicted automatically, even if the disk is full. This is not a new CAM
    object store. Keep this directory private and do not expose it over HTTP.
    """
    def __init__(self,journal):
        journal._check_open()
        self.journal=journal
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

    def stage(self,key,task_id,data,verify_task_media):
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
            metadata=validate_native_mp4(data,record["intent"]["identity"]["ratio"])
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