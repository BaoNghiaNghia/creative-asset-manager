from __future__ import annotations

import base64
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image


ENCODER_ROOT = Path(__file__).resolve().parents[1]
if str(ENCODER_ROOT) not in sys.path:
    sys.path.insert(0, str(ENCODER_ROOT))

from benchmark_load import _host_memory_snapshot, _jpeg_payloads


def test_load_benchmark_generates_unique_valid_jpeg_payloads(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8), (120, 80, 40)).save(source)

    payloads = _jpeg_payloads(source, 4)

    assert len(payloads) == 4
    assert len(set(payloads)) == 4
    for payload in payloads:
        raw = base64.b64decode(payload)
        with Image.open(BytesIO(raw)) as image:
            assert image.format == "JPEG"
            assert image.size == (8, 8)


def test_host_memory_snapshot_reports_available_memory_and_swap(
    tmp_path: Path,
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       8192000 kB\n"
        "MemAvailable:   3145728 kB\n"
        "SwapTotal:      2097152 kB\n"
        "SwapFree:       1572864 kB\n",
        encoding="utf-8",
    )

    snapshot = _host_memory_snapshot(meminfo)

    assert snapshot == {
        "memory_available_bytes": 3145728 * 1024,
        "swap_total_bytes": 2097152 * 1024,
        "swap_used_bytes": (2097152 - 1572864) * 1024,
    }
