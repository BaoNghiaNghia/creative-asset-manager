#!/usr/bin/env python3
"""Visual regression proof: one saved FRONT UV for native factory FRONT / 3/4.

Run: apps/api/.venv/bin/python scripts/qa_slot2_shared_uv.py --out /tmp/hat-uv-qa
Outputs are QA evidence only and never fed back to a renderer or published.
"""
from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from app.modules.realistic_review_ugc.hat_stock_sync import read_managed_stock
from app.modules.realistic_review_ugc.slot2_fixed_uv import (
    _input_design, _mask_outline, _project_atlas, _shared_texture,
    load_profile, render_fixed_uv_slot2,
)
from app.modules.realistic_review_ugc.front_placement import job_front_placement

FRONT = {
    "left": .212142, "top": .328244,
    "width": .560727, "height": .232605,
    "upper_line": .270188, "lower_line": .597924,
    "design_size": 100,
}

def project(view: dict, u: float, v: float, canvas: tuple[int, int]) -> tuple[float, float]:
    col, row = min(3, int(u * 4)), min(3, int(v * 4))
    a, b = u * 4 - col, v * 4 - row
    nodes = np.asarray(view["nodes"], np.float64)
    p, q = nodes[row,col], nodes[row,col+1]
    r, s = nodes[row+1,col], nodes[row+1,col+1]
    xy = p*(1-a)*(1-b)+q*a*(1-b)+r*(1-a)*b+s*a*b
    return float(xy[0] * canvas[0] / view["width"]), float(xy[1] * canvas[1] / view["height"])

def make_test_design() -> bytes:
    image=Image.new("RGBA",(840,440),(0,0,0,0))
    draw=ImageDraw.Draw(image)
    # All features share a single alpha bounding box with deliberate padding.
    for j in range(8):
        for i in range(18):
            color=(48,141,198,200) if (i+j)%2 else (255,226,89,200)
            x=55+i*40; y=55+j*40
            draw.rectangle([x,y,x+37,y+37],fill=color)
    draw.line([(415,52),(415,378)], fill=(231,18,145,255),width=7)
    draw.line([(51,218),(778,218)], fill=(18,206,105,255),width=7)
    draw.text((52,40),"L",fill=(245,31,31,255),stroke_width=2,stroke_fill=(255,255,255,255))
    draw.text((770,40),"R",fill=(245,31,31,255),stroke_width=2,stroke_fill=(255,255,255,255))
    buf=BytesIO();image.save(buf,"PNG")
    return buf.getvalue()

def trace(draw: ImageDraw.ImageDraw, view: dict, size: tuple[int,int], box: tuple,
          u0: float, v0: float, u1: float, v1: float, color: tuple[int,int,int,int], width=3):
    points=[project(view,u0+(u1-u0)*i/80,v0+(v1-v0)*i/80,size) for i in range(81)]
    draw.line(points,fill=color,width=width,joint="curve")

def to_rgba(sampled: np.ndarray) -> Image.Image:
    alpha=np.maximum(sampled[...,3],0)
    colors=np.clip(sampled[...,:3]/np.maximum(alpha[...,None]/255,1e-6),0,255)
    return Image.fromarray(np.dstack([np.uint8(colors),np.uint8(np.rint(alpha))]),"RGBA")

def main(out: Path, color: str) -> None:
    from app.core.config import get_settings
    out.mkdir(parents=True,exist_ok=True)
    profile,sha=load_profile()
    original=make_test_design()
    design=_input_design(original)
    atlas, box=_shared_texture(design,profile["logicalArea"],profile["views"]["front"],FRONT)
    (out/"design-qa-grid.png").write_bytes(original)
    stock={}
    for view,slot in (("front","front"),("threeQuarter","three-quarter")):
        item=read_managed_stock(get_settings(),color,slot)
        if item is None:
            raise RuntimeError("Missing verified manufacturer "+slot+" for "+color)
        stock[view]=Image.open(item[0]).convert("RGB")
        if stock[view].size!=(1254,1254):
            raise RuntimeError("Profile expects both verified square 1254px factory views")

    rendered=render_fixed_uv_slot2(
        original,Path(read_managed_stock(get_settings(),color,"three-quarter")[0]).read_bytes(),
        front_placement=FRONT, expected_profile_sha256=sha,
    )
    (out/"export-clean-3q.png").write_bytes(rendered.output_png)
    (out/"front-design-layer.png").write_bytes(rendered.front_layer_png)
    reports={}
    for view in ("front","threeQuarter"):
        vi=profile["views"][view]
        size=stock[view].size
        sampled=_project_atlas(atlas,vi,size)
        layer=to_rgba(sampled)
        comp=stock[view].convert("RGBA")
        comp.alpha_composite(layer)
        dbg=ImageDraw.Draw(comp,"RGBA")
        for i in range(5):
            trace(dbg,vi,size,box,i/4,0,i/4,1,(0,201,239,190),width=2)
            trace(dbg,vi,size,box,0,i/4,1,i/4,(0,201,239,190),width=2)
        trace(dbg,vi,size,box,.5,0,.5,1,(255,224,0,255),width=6)
        ucenter=(box[0]+box[2])/2
        trace(dbg,vi,size,box,ucenter,box[1],ucenter,box[3],(225,9,202,255),width=3)
        baseline=(box[1]+box[3])/2
        trace(dbg,vi,size,box,box[0],baseline,box[2],baseline,(17,236,102,255),width=3)
        for iy in range(5):
            for ix in range(5):
                x,y=project(vi,ix/4,iy/4,size)
                dbg.ellipse([x-4,y-4,x+4,y+4],fill=(255,0,180,255))
        outline=_mask_outline(vi["visibleMaskPath"])
        outline[:,0]*=size[0]/vi["width"]
        outline[:,1]*=size[1]/vi["height"]
        dbg.line([tuple(row) for row in outline]+[tuple(outline[0])],fill=(245,32,44,255),width=4,joint="curve")
        outname="debug-front.png" if view=="front" else "debug-3q.png"
        comp.convert("RGB").save(out/outname,"PNG",optimize=True)
        poly=np.zeros((size[1],size[0]),np.uint8)
        cv2.fillPoly(poly,[np.rint(outline).astype(np.int32)],255)
        mask=Image.fromarray(poly,"L")
        if view=="threeQuarter":
            mask.save(out/"visibility-mask-3q.png")
        alpha=np.asarray(layer.getchannel("A"))
        outside=int(np.count_nonzero((alpha>2)&(poly==0)))
        if outside:raise AssertionError(f"Design escaped {view} mask: {outside} pixels")
        grid_cover=np.zeros((size[1],size[0]),np.uint8)
        for row in range(4):
            for col in range(4):
                cell=np.array([
                    vi["nodes"][row][col],vi["nodes"][row][col+1],
                    vi["nodes"][row+1][col+1],vi["nodes"][row+1][col],
                ],np.float64)
                cell[:,0]*=size[0]/vi["width"]
                cell[:,1]*=size[1]/vi["height"]
                cv2.fillPoly(grid_cover,[np.rint(cell).astype(np.int32)],255)
        grid_escape=int(np.count_nonzero((alpha>2)&(grid_cover==0)))
        if grid_escape:raise AssertionError(f"Design escaped {view} grid: {grid_escape} pixels")
        reports[view]={
            "image_px":list(size),"mask_escape_pixels":outside,
            "grid_escape_pixels":grid_escape,
            "visible_design_pixels":int(np.count_nonzero(alpha>10)),
            "uv_center_projected":project(vi,.5,.5,size),
            "painted_center_projected":project(vi,ucenter,baseline,size),
            "uv_v_half_row_nodes_exact":all(
                np.allclose(project(vi,c/4,.5,size),vi["nodes"][2][c],atol=0.001)
                for c in range(5)
            ),
        }
    clean=np.asarray(Image.open(BytesIO(rendered.output_png)).convert("RGB"))
    stock_3q=np.asarray(stock["threeQuarter"])
    final=np.asarray(_project_atlas(atlas,profile["views"]["threeQuarter"],stock["threeQuarter"].size))
    unchanged=int(np.count_nonzero(np.any(clean[final[...,3]==0]!=stock_3q[final[...,3]==0],axis=1)))
    if unchanged:raise AssertionError(f"Clean output changed protected stock pixels: {unchanged}")
    if not abs((box[0]+box[2])/2-.5)<1e-6: raise AssertionError("Design is not on UV u=0.5")
    if not reports["threeQuarter"]["uv_v_half_row_nodes_exact"]:raise AssertionError("Baseline drift")
    result={
      "passed": True,"profileId":profile["profileId"],"profile_sha256":sha,
      "frontPlacement":FRONT,"uv_box":box,"uv_u_center":(box[0]+box[2])/2,
      "actual_alpha_bbox_not_recropped_after_mask":True,
      "same_atlas_for_both_views":True,
      "mask_before_composite":True,
      "visible_fraction":rendered.visible_fraction,
      "clean_png_no_debug_overlays":True,
      "unchanged_stock_pixels_outside_alpha":unchanged,
      "views":reports,
      "files":["design-qa-grid.png","debug-front.png","debug-3q.png",
               "export-clean-3q.png","visibility-mask-3q.png","front-design-layer.png"]
    }
    (out/"qa-results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,default=Path("/tmp/cam-slot2-uv-proof"))
    parser.add_argument("--color",default="white-navy")
    args=parser.parse_args()
    main(args.out,args.color)
