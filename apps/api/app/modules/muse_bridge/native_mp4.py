"""Read-only native MP4 container checks. No decoding, external binaries or re-encoding.

Metadata is optional container-declared information, not an independent visual
or bitstream QA result. Preserve the exact provider bytes.
"""
from __future__ import annotations

MIN_VIDEO_BYTES = 2048
MAX_VIDEO_BYTES = 40 * 1024 * 1024


class InvalidNativeMP4(ValueError):
    pass


def _boxes(data, start, end):
    pos = start
    count = 0
    while pos < end:
        count += 1
        if count > 10000 or pos + 8 > end:
            raise InvalidNativeMP4("Malformed MP4 box")
        size = int.from_bytes(data[pos:pos+4], "big")
        name = data[pos+4:pos+8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise InvalidNativeMP4("Truncated MP4 box")
            size = int.from_bytes(data[pos+8:pos+16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise InvalidNativeMP4("Truncated MP4 box")
        yield name, pos + header, pos + size
        pos += size


def _duration(data, start, end):
    # mvhd version 0/1 stores timescale and duration before its matrix.
    version = data[start] if start < end else -1
    offset, length = (12, 4) if version == 0 else (20, 8) if version == 1 else (0, 0)
    if not length or start + offset + 4 + length > end:
        return None
    scale = int.from_bytes(data[start+offset:start+offset+4], "big")
    ticks = int.from_bytes(data[start+offset+4:start+offset+4+length], "big")
    if not scale or not ticks or ticks == (1 << (8*length))-1:
        return None
    return round(ticks / scale, 2)


def inspect_native_mp4(data):
    if not isinstance(data, bytes) or not MIN_VIDEO_BYTES <= len(data) <= MAX_VIDEO_BYTES:
        raise InvalidNativeMP4("Invalid MP4 size")
    top = list(_boxes(data, 0, len(data)))
    if not top or top[0][0] != b"ftyp" or top[0][2]-top[0][1] < 8:
        raise InvalidNativeMP4("Missing MP4 file type")
    moov = next(((s,e) for name,s,e in top if name == b"moov" and s<e), None)
    if moov is None or not any(name==b"mdat" and s<e for name,s,e in top):
        raise InvalidNativeMP4("Incomplete MP4 container")
    metadata = {"mediaValidation": "native_container_only"}
    # Read existing header information for UI; never decode or modify video.
    # Fragmented files may omit duration in mvhd. Do not invent it.
    try:
        for name, start, end in _boxes(data, *moov):
            if name == b"mvhd":
                duration = _duration(data,start,end)
                if duration is not None:
                    metadata["durationSeconds"] = duration
            elif name == b"trak" and "width" not in metadata:
                children = list(_boxes(data,start,end))
                is_video = False
                for kind,s,e in children:
                    if kind == b"mdia":
                        for h,hs,he in _boxes(data,s,e):
                            if h == b"hdlr" and he-hs>=12 and data[hs+8:hs+12]==b"vide":
                                is_video = True
                if not is_video:
                    continue
                tk = next(((s,e) for kind,s,e in children if kind==b"tkhd"),None)
                if tk and tk[1]-tk[0]>=84:
                    s,e = tk
                    # Display dimensions are 16.16 fixed point. A quarter-turn
                    # track matrix swaps displayed width/height.
                    width = int.from_bytes(data[e-8:e-4],"big") / 65536
                    height = int.from_bytes(data[e-4:e],"big") / 65536
                    a = int.from_bytes(data[e-44:e-40],"big",signed=True)
                    b = int.from_bytes(data[e-40:e-36],"big",signed=True)
                    c = int.from_bytes(data[e-32:e-28],"big",signed=True)
                    d = int.from_bytes(data[e-28:e-24],"big",signed=True)
                    if abs(b)>abs(a) and abs(c)>abs(d):
                        width,height=height,width
                    if 0<width<=16384 and 0<height<=16384:
                        metadata.update(width=round(width),height=round(height))
    except InvalidNativeMP4:
        # Container integrity already checked at top level; optional
        # metadata not available for this provider export.
        pass
    return metadata
