"""Large immutable ImageGen design uploads retain PNG integrity and tight legacy caps."""
import binascii
import struct
import zlib

import pytest
from fastapi import HTTPException

from app.modules.etsy_spy.image_agent import (
    MAX_DESIGN_PNG_BYTES,
    MAX_DESIGN_RECEIPT_BYTES,
    MAX_PNG_BYTES,
    MAX_RECEIPT_BYTES,
    _verify_png,
)


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _large_structural_png() -> bytes:
    # Structural validation intentionally does NOT decode 24 MP of pixel data.
    # The 25 MB ancillary chunk models a large but lossless bounded PNG.
    header = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
    return (bytes([137, 80, 78, 71, 13, 10, 26, 10])
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"\\x00"))
            + _chunk(b"tEXt", b"x" * (23 * 1024 * 1024))
            + _chunk(b"IEND", b""))


def test_large_design_png_preserves_legacy_receipt_limits() -> None:
    png = _large_structural_png()
    assert MAX_PNG_BYTES == 22_000_000
    assert MAX_RECEIPT_BYTES == 31_000_000
    assert len(png) > MAX_PNG_BYTES
    assert len(png) < MAX_DESIGN_PNG_BYTES
    assert (4 * ((MAX_DESIGN_PNG_BYTES + 2) // 3)) < MAX_DESIGN_RECEIPT_BYTES

    with pytest.raises(HTTPException) as old:
        _verify_png(png)
    assert old.value.status_code == 422
    _verify_png(png, max_bytes=MAX_DESIGN_PNG_BYTES)


def test_png_crc_is_still_checked_after_large_upload_enablement() -> None:
    png = bytearray(_large_structural_png())
    png[-15] ^= 1
    with pytest.raises(HTTPException) as invalid:
        _verify_png(bytes(png), max_bytes=MAX_DESIGN_PNG_BYTES)
    assert invalid.value.status_code == 422
