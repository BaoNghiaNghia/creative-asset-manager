"""Private immutable PNG staging. No gallery, Drive, or renderer side effects."""
import fcntl
import hashlib
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

MAX_BYTES = 32 * 1024**2
MAX_TOTAL_BYTES = 192 * 1024**2
MIN_FREE_BYTES = 2 * 1024**3
NAME = re.compile(r'[a-f0-9]{64}\.png\Z')

class StageError(RuntimeError):
    pass

def directory():
    root = Path(os.environ.get('CAM_MUSE_IMAGE_LITE_STAGE_DIR',
        '/var/lib/creative-asset-manager/muse-image-lite-stage'))
    try:
        if not root.is_absolute() or root.resolve() != root:
            raise StageError('image_stage_directory_invalid')
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise StageError('image_stage_directory_invalid')
        return root
    except OSError:
        raise StageError('image_stage_unavailable') from None

def filename(tenant, cid, epoch, attempt, sha):
    # One physical file per attempt, even if the DB commit/ACK was lost.
    # The separately checked SHA is not part of the filename.
    return hashlib.sha256('\0'.join((tenant,cid,str(epoch),attempt)).encode()).hexdigest()+'.png'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def read(name, sha, size):
    if not isinstance(name, str) or not NAME.fullmatch(name) or type(size) is not int or not 57 <= size <= MAX_BYTES:
        raise StageError('image_stage_identity_invalid')
    try:
        fd = os.open(directory()/name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            s = os.fstat(stream.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_size != size:
                raise StageError('image_stage_identity_invalid')
            raw = stream.read(MAX_BYTES+1)
        if len(raw) != size or digest(raw) != sha:
            raise StageError('image_stage_checksum_mismatch')
        return raw
    except OSError:
        raise StageError('image_stage_unavailable') from None

def store(name, sha, raw):
    if not NAME.fullmatch(name) or not 57 <= len(raw) <= MAX_BYTES or digest(raw) != sha:
        raise StageError('image_stage_identity_invalid')
    root = directory()
    try:
        fd = os.open(root/'.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
        with os.fdopen(fd,'rb+') as lock:
            s = os.fstat(lock.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
                raise StageError('image_stage_directory_invalid')
            fcntl.flock(lock, fcntl.LOCK_EX)
            if os.path.lexists(root/name):
                read(name,sha,len(raw)); return
            total=0
            for entry in root.iterdir():
                s=entry.lstat()
                if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid():
                    raise StageError('image_stage_directory_invalid')
                total += s.st_size
            if total+len(raw)>MAX_TOTAL_BYTES or shutil.disk_usage(root).free-len(raw)<MIN_FREE_BYTES:
                raise StageError('image_stage_capacity_hold')
            tmp=None
            try:
                with tempfile.NamedTemporaryFile(dir=root,prefix='.incoming-',delete=False) as out:
                    tmp=Path(out.name); os.fchmod(out.fileno(),0o600)
                    out.write(raw); out.flush(); os.fsync(out.fileno())
                os.link(tmp,root/name,follow_symlinks=False)
                tmp.unlink(); tmp=None
                dfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
                try: os.fsync(dfd)
                finally: os.close(dfd)
                read(name,sha,len(raw))
            finally:
                if tmp is not None: tmp.unlink(missing_ok=True)
    except OSError:
        raise StageError('image_stage_unavailable') from None
