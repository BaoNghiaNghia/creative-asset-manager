"""CAM V4 worker-side renderer.

Run on the Muse render worker (where longer renders do not hold a CAM HTTP
request open). Output remains local *only until* its Drive upload succeeds.
Example:
  python -m app.modules.muse_bridge.fabric_video_cli \
    --blank plain-9x16.mp4 --master approved-native.png \
    --geometry take-geometry.json --aspect vertical \
    --output printed-9x16.mp4 --proof printed-9x16.proof.json \
    --expected-master-sha <64hex>

Do not run on a previously printed/approved MP4. Do not overwrite outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from .fabric_video_compositor import (
    CONTRACT,MAX_SOURCE_MP4_BYTES,composite_blank_c1717_video,
)


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="CAM C1717 tracked-cotton native ink compositor")
    parser.add_argument("--blank",type=Path,required=True)
    parser.add_argument("--master",type=Path,required=True)
    parser.add_argument("--geometry",type=Path,required=True)
    parser.add_argument("--aspect",choices=("vertical","horizontal"),required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--proof",type=Path,required=True)
    parser.add_argument("--expected-master-sha",required=True)
    opts=parser.parse_args(argv)
    for path in (opts.output,opts.proof):
        if path.exists():
            parser.error("Existing outputs are immutable; never overwrite a completed video")
    if opts.blank.resolve()==opts.output.resolve():
        parser.error("Printed output must not replace original blank MP4")
    if opts.expected_master_sha != hashlib.sha256(opts.master.read_bytes()).hexdigest():
        parser.error("Native Master SHA does not match CAM-approved master")
    if opts.blank.stat().st_size > MAX_SOURCE_MP4_BYTES:
        parser.error("Source video exceeds CAM 40MiB limit")
    geometry=json.loads(opts.geometry.read_text(encoding="utf-8"))
    if set(geometry) != {"takeA","takeB","cutSecond"}:
        parser.error("Expected exactly takeA,takeB,cutSecond")
    if geometry["cutSecond"]!=6.0:
        parser.error("V4 story requires a motivated 6-second cut")
    out,proof=composite_blank_c1717_video(
        plain_mp4=opts.blank.read_bytes(),
        artwork_png=opts.master.read_bytes(),
        aspect=opts.aspect,
        take_a=geometry["takeA"],
        take_b=geometry["takeB"],
        cut_second=6.0,
    )
    # Atomically make proof and output available to the uploader. The worker
    # must remove these temporary files after CAM verifies Drive storage.
    with opts.output.open("xb") as handle:
        handle.write(out)
    with opts.proof.open("x",encoding="utf-8") as handle:
        json.dump(proof,handle,ensure_ascii=False,indent=2)
    print(json.dumps({
        "contract":CONTRACT,
        "outputSha256":proof["printedVideoSha256"],
        "sourceSha256":proof["plainVideoSha256"],
        "durationSeconds":proof["durationSeconds"],
        "framesProcessed":proof["framesProcessed"],
        "fabricSurfaceQa":proof["manualFabricSurfaceQa"],
        "output":str(opts.output),
        "proof":str(opts.proof),
    },ensure_ascii=False))
    return 0


if __name__=="__main__":
    sys.exit(main())
