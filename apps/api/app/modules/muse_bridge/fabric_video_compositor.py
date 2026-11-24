"""CAM V4 experimental C1717 video compositor: native print follows moving fabric.

Input: an approved BLANK short-sleeve C1717 MP4 with a real tracked cotton
surface; same approved RGBA Native Master; independent measured physical
geometry for each of two six-second takes. All content/geometry attestation
is still independent QA. Does not hallucinate an article of clothing or print.

This is an explicit PILOT feature, not evidence of a 3D cloth renderer.
Dense optical flow warps a source artwork's pixels frame to frame; it cannot
recover invisible fabric, make up a reliable physical scale, or replace 3D
occlusion/UV tracking in difficult poses. In those cases HOLD, never PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np
from PIL import Image
from fastapi import HTTPException

from .calibrated_hero import placement_plan

CONTRACT = "CAM_C1717_V4_FABRIC_FLOW_COMPOSITE_PILOT_V1"
QA_HOLD = "FABRIC_SURFACE_TRACKING_BLOCKED"
MAX_SOURCE_MP4_BYTES = 40 * 1024 * 1024
MAX_MASTER_BYTES = 20 * 1024 * 1024
MAX_VIDEO_PIXELS = 1_000_000
MAX_FRAMES = 400
MAX_FPS = 30
MIN_TRACKABLE_FEATURES = 6


@dataclass(frozen=True)
class FabricTake:
    """Two independently measured take-start fabric surfaces."""
    lowerCollarBottomYpx: float
    garmentReferenceWidthPx: float
    garmentReferenceWidthMm: float
    printCenterXpx: float
    printWidthMm: float
    shirtHemYpx: float
    calibrationUncertaintyMm: float
    plainC1717ShirtVerified: bool
    frontFabricPlaneMeasured: bool
    noExistingPrintOnPlainSourceVerified: bool
    garmentPolygonPx: tuple[tuple[float, float], ...]

    @classmethod
    def parse(cls, data: dict):
        try:
            polygon = tuple(tuple(float(v) for v in pt) for pt in data["garmentPolygonPx"])
            if len(polygon) < 4 or len(polygon) > 16 or any(len(p) != 2 for p in polygon):
                raise ValueError("polygon")
            mapped = {field: data[field] for field in (
                "lowerCollarBottomYpx", "garmentReferenceWidthPx",
                "garmentReferenceWidthMm", "printCenterXpx", "printWidthMm",
                "shirtHemYpx", "calibrationUncertaintyMm",
                "plainC1717ShirtVerified", "frontFabricPlaneMeasured",
                "noExistingPrintOnPlainSourceVerified"
            )}
            out = cls(garmentPolygonPx=polygon, **mapped)
            if (not all(type(getattr(out, flag)) is bool and getattr(out, flag)
                        for flag in ("plainC1717ShirtVerified",
                                     "frontFabricPlaneMeasured",
                                     "noExistingPrintOnPlainSourceVerified"))
                    or type(out.calibrationUncertaintyMm) not in (int, float)
                    or not math.isfinite(out.calibrationUncertaintyMm)
                    or not 0 <= out.calibrationUncertaintyMm <= 2.5):
                raise ValueError("fabric-plane evidence")
            return out
        except (TypeError, KeyError, ValueError, OverflowError) as exc:
            raise HTTPException(422, "Unverified blank C1717 cloth polygon or calibration") from exc


def _native_rgba(master_png: bytes) -> tuple[np.ndarray, str]:
    if not 256 <= len(master_png) <= MAX_MASTER_BYTES:
        raise HTTPException(422, "Native Master PNG has invalid size")
    digest = hashlib.sha256(master_png).hexdigest()
    try:
        with Image.open(BytesIO(master_png)) as im:
            if im.format != "PNG" or getattr(im, "n_frames", 1) != 1:
                raise ValueError("PNG")
            if im.width * im.height > 24_000_000 or max(im.width, im.height) > 8192:
                raise ValueError("too large")
            art = im.convert("RGBA")
            bbox = art.getchannel("A").getbbox()
            if bbox is None:
                raise ValueError("empty alpha")
            alpha_extrema = art.getchannel("A").getextrema()
            if alpha_extrema[0] == 255:
                raise ValueError("opaque PNG")
            rgba = np.array(art.crop(bbox), dtype=np.uint8)
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Approved Native Master must have transparent pixels") from exc
    return rgba, digest


def _poly_mask(take: FabricTake, width: int, height: int) -> np.ndarray:
    pts = np.array(take.garmentPolygonPx, dtype=np.float32)
    if (not np.isfinite(pts).all() or
            np.any(pts[:, 0] < 0) or np.any(pts[:, 0] >= width) or
            np.any(pts[:, 1] < 0) or np.any(pts[:, 1] >= height)):
        raise HTTPException(422, "Fabric polygon outside video frame")
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 255)
    if np.count_nonzero(mask) < 10_000:
        raise HTTPException(422, "Measured shirt plane too small")
    return mask


def _new_take_layer(frame_bgr: np.ndarray, take: FabricTake,
                    artwork: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict, np.ndarray]:
    h, w = frame_bgr.shape[:2]
    art_h, art_w = artwork.shape[:2]
    placement = placement_plan(
        width_px=w, height_px=h,
        lower_collar_y_px=take.lowerCollarBottomYpx,
        garment_reference_width_px=take.garmentReferenceWidthPx,
        garment_reference_width_mm=take.garmentReferenceWidthMm,
        print_center_x_px=take.printCenterXpx, print_width_mm=take.printWidthMm,
        shirt_hem_y_px=take.shirtHemYpx, artwork_ratio=art_w / art_h,
    )
    if (placement["calculatedGapMm"] + take.calibrationUncertaintyMm > 155.0 or
            placement["calculatedGapMm"] - take.calibrationUncertaintyMm < 145.0):
        raise HTTPException(422, "Physical print offset uncertainty exceeds 150mm tolerance")
    left, top, right, bottom = placement["inkBoundingBoxPx"]
    polygon = _poly_mask(take, w, h)
    if np.mean(polygon[top:bottom, left:right]) < 245:
        raise HTTPException(422, "Artwork crosses measured shirt boundary")
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    # Features are measured in the unprinted source shirt, NOT artwork pixels.
    track_region = cv2.dilate(
        np.where(polygon > 0, 255, 0).astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
    )
    cy = int((top + bottom) / 2)
    cx = int((left + right) / 2)
    rh, rw = max(70, int((bottom-top)*1.5)), max(70, int((right-left)*1.5))
    feature_roi = np.zeros_like(track_region)
    cv2.rectangle(feature_roi, (max(0,cx-rw),max(0,cy-rh)),
                  (min(w-1,cx+rw),min(h-1,cy+rh)), 255, -1)
    features = cv2.goodFeaturesToTrack(
        gray, mask=cv2.bitwise_and(track_region, feature_roi),
        maxCorners=120, qualityLevel=0.008, minDistance=7, blockSize=7,
    )
    feature_count = 0 if features is None else len(features)
    if feature_count < MIN_TRACKABLE_FEATURES:
        raise HTTPException(
            422, f"{QA_HOLD}: blank garment has insufficient visual texture "
                 f"for trustworthy tracking ({feature_count} tracked corners)"
        )
    ink = cv2.cvtColor(artwork[:,:,:3], cv2.COLOR_RGB2BGR)
    alpha = artwork[:,:,3].astype(np.float32) / 255.0
    width, height = right-left, bottom-top
    # Gentle cylindrical front-chest geometry at start: edges recede as
    # a torso curves. Rest of motion comes from optical flow, not rigid XY.
    x_coord = (np.arange(width, dtype=np.float32)+0.5)/width
    center = (x_coord-.5)*2
    angle = 0.55
    u = np.arcsin(np.clip(center*np.sin(angle), -.999, .999))/angle
    map_x = (u+1)*.5*art_w
    xx = np.broadcast_to(map_x[None,:], (height,width)).astype(np.float32)
    yy = np.broadcast_to(
        ((np.arange(height,dtype=np.float32)+.5)/height*art_h)[:,None],
        (height,width)).astype(np.float32)
    resized_ink = cv2.remap(ink,xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    resized_alpha = cv2.remap(alpha,xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    rgb_layer = np.zeros((h,w,3),dtype=np.uint8)
    alpha_layer = np.zeros((h,w),dtype=np.float32)
    rgb_layer[top:bottom,left:right] = resized_ink
    alpha_layer[top:bottom,left:right] = resized_alpha
    alpha_layer *= polygon.astype(np.float32)/255
    placement["trackedFabricFeatureCountAtStart"] = feature_count
    placement["calibrationUncertaintyMm"] = take.calibrationUncertaintyMm
    placement["physicalPlacementIndependentQa"] = "pending_review"
    return rgb_layer,alpha_layer,polygon,placement,gray


def _deform_from_previous(prev_gray: np.ndarray, current_gray: np.ndarray,
                          ink_bgr: np.ndarray, alpha: np.ndarray,
                          garment_mask: np.ndarray) -> tuple[np.ndarray,np.ndarray,np.ndarray,dict]:
    h,w = prev_gray.shape
    # Half-resolution scene flow, bounded for the single-VPS pilot. The
    # remap is inverse: current pixel -> previous fabric pixel.
    small_w = min(w, 400)
    small_h = max(1, int(round(h * small_w / w)))
    prev_small = cv2.resize(prev_gray,(small_w,small_h),interpolation=cv2.INTER_AREA)
    curr_small = cv2.resize(current_gray,(small_w,small_h),interpolation=cv2.INTER_AREA)
    # backward displacement: where each CURRENT fabric pixel came from.
    backward = cv2.calcOpticalFlowFarneback(
        curr_small, prev_small, None,
        pyr_scale=0.5, levels=3, winsize=19, iterations=3,
        poly_n=5, poly_sigma=1.2, flags=0,
    )
    dx = cv2.resize(backward[:,:,0], (w,h), interpolation=cv2.INTER_LINEAR)*(w/small_w)
    dy = cv2.resize(backward[:,:,1], (w,h), interpolation=cv2.INTER_LINEAR)*(h/small_h)
    step = np.sqrt(dx*dx+dy*dy)
    old_roi = alpha > 0.1
    if np.count_nonzero(old_roi) < 200:
        raise HTTPException(422, f"{QA_HOLD}: lost printed fabric region")
    median_motion = float(np.median(step[old_roi]))
    p99_motion = float(np.percentile(step[old_roi],99))
    if not math.isfinite(p99_motion) or p99_motion > max(35.0,w*.09):
        raise HTTPException(422,f"{QA_HOLD}: untrackable fast fabric displacement")
    # Gaussian-smooth field slightly, removing numerical ripples without
    # flattening real global wrinkle folds (which use 19px input windows).
    dx = cv2.GaussianBlur(dx,(5,5),0)
    dy = cv2.GaussianBlur(dy,(5,5),0)
    xs = np.broadcast_to(np.arange(w,dtype=np.float32)[None,:],(h,w))
    ys = np.broadcast_to(np.arange(h,dtype=np.float32)[:,None],(h,w))
    map_x = (xs+dx).astype(np.float32)
    map_y = (ys+dy).astype(np.float32)
    moved_bgr = cv2.remap(ink_bgr,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    moved_alpha = cv2.remap(alpha,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    moved_mask = cv2.remap(garment_mask,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    # Texture-consistency confidence: reverse-warp previous shirt and
    # compare with current background in the *fabric* region. A camera cut
    # resets the take; otherwise a large error means tracking is not safe.
    prev_warped = cv2.remap(prev_gray,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    examined = moved_alpha > 0.2
    residual = float(np.median(np.abs(
        prev_warped[examined].astype(np.float32)-current_gray[examined].astype(np.float32)
    ))) if np.count_nonzero(examined) > 200 else 255.0
    if residual > 40:
        raise HTTPException(422, f"{QA_HOLD}: poor optical flow alignment to cotton texture")
    return moved_bgr,moved_alpha,moved_mask,{
        "medianMotionPx": round(median_motion,3),
        "p99MotionPx": round(p99_motion,3),
        "medianFabricResidual": round(residual,3),
    }


def _blend_ink(frame_bgr: np.ndarray, ink_bgr: np.ndarray, alpha: np.ndarray,
               garment_mask: np.ndarray, reference_light: float,
               blank_shirt_lab: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr,cv2.COLOR_BGR2GRAY).astype(np.float32)
    near = cv2.GaussianBlur(gray,(0,0),sigmaX=4.0)
    far = cv2.GaussianBlur(gray,(0,0),sigmaX=24.0)
    wrinkle = np.clip((near+10)/(far+10),.72,1.24)
    garment_pixels = garment_mask > 128
    current_light = float(np.mean(far[garment_pixels])) if np.any(garment_pixels) else reference_light
    global_light = np.clip((current_light+20)/(reference_light+20),.78,1.25)
    shade = np.clip(wrinkle*global_light,.72,1.2)
    # Natural matte screen print: moving cloth shadows affect ink. The
    # image under print supplies fine cotton weave, but original letters
    # are never re-generated or re-spelled.
    opaque = np.clip(alpha * (garment_mask.astype(np.float32)/255),0,1)
    # Conservative occlusion matte: skin, hands and objects whose color
    # clearly differs from the measured blank shirt suppress the ink.
    # Similar-colored occluders remain an explicit human QA limitation.
    lab=cv2.cvtColor(frame_bgr,cv2.COLOR_BGR2LAB).astype(np.float32)
    distance=np.linalg.norm(lab-blank_shirt_lab[None,None,:],axis=2)
    matching_fabric=np.clip((72-distance)/28,0,1)
    matching_fabric=cv2.GaussianBlur(matching_fabric,(3,3),0)
    opaque *= matching_fabric
    if np.max(opaque) < .01:
        raise HTTPException(422,f"{QA_HOLD}: source print is out of garment bounds")
    foreground = ink_bgr.astype(np.float32)*shade[:,:,None]
    base = frame_bgr.astype(np.float32)
    # Hold some cloth texture through ink but prevent severe color mutation.
    detail = np.clip((gray-near)*.08,-6,6)[:,:,None]
    foreground = np.clip(foreground+detail,0,255)
    mixed = base*(1-opaque[:,:,None]) + foreground*opaque[:,:,None]
    return np.uint8(np.clip(mixed,0,255))


def composite_blank_c1717_video(
    *, plain_mp4: bytes, artwork_png: bytes, aspect: str,
    take_a: dict, take_b: dict, cut_second: float = 6.0,
    max_duration: float = 12.3, test_mode: bool = False,
) -> tuple[bytes,dict]:
    """Return H264 MP4 plus evidence. Does not store or approve any output.

    Capture/encode temporary files are deleted even when tracking fails.
    No source video can be silently transformed into a second aspect.
    """
    if not 2048 <= len(plain_mp4) <= MAX_SOURCE_MP4_BYTES or plain_mp4[4:8]!=b"ftyp":
        raise HTTPException(422,"Bounded original blank-shirt MP4 required")
    if aspect not in ("vertical","horizontal"):
        raise HTTPException(422,"Unknown original video ratio")
    if not test_mode and cut_second != 6.0:
        raise HTTPException(422,"V3.3/V4 requires two real six-second takes")
    art, master_sha = _native_rgba(artwork_png)
    takes = (FabricTake.parse(take_a), FabricTake.parse(take_b))
    with tempfile.TemporaryDirectory(prefix="cam-v4-") as td:
        source = Path(td)/"plain.mp4"
        destination = Path(td)/"printed.mp4"
        source.write_bytes(plain_mp4)
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise HTTPException(422,"Blank source MP4 cannot be decoded")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        reported_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = reported_frames/fps if fps>0 else 0
        ratio = width/height if height else 0
        target_ratio = 9/16 if aspect=="vertical" else 16/9
        if (not math.isfinite(fps) or fps < (6 if test_mode else 12) or fps > MAX_FPS
                or not 256 <= width <= 1920 or not 256 <= height <= 1920
                or width*height > MAX_VIDEO_PIXELS or reported_frames > MAX_FRAMES
                or abs(ratio-target_ratio) > .07
                or duration > max_duration or duration <= cut_second+.3
                or not test_mode and not 11.7 <= duration <= 12.3):
            capture.release()
            raise HTTPException(422,"Unverified ratio, frame limit, cut or duration")
        cut_frame = int(round(cut_second*fps))
        proc = subprocess.Popen(
            ["ffmpeg","-hide_banner","-loglevel","error","-y",
             "-f","rawvideo","-vcodec","rawvideo","-pix_fmt","bgr24",
             "-s",f"{width}x{height}","-r",f"{fps:.6f}","-i","pipe:0",
             "-an","-c:v","libx264","-preset","veryfast","-crf","19",
             "-pix_fmt","yuv420p","-movflags","+faststart",str(destination)],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
        )
        metrics=[]
        segments=[]
        movement_by_take = [[], []]
        prev_gray=None; layer=None; alpha=None; garment=None; light=1.
        blank_shirt_lab=None
        try:
            count=0
            while True:
                ok,frame = capture.read()
                if not ok:break
                if count >= MAX_FRAMES:
                    raise HTTPException(413,"Excessive frame count")
                if count in (0,cut_frame):
                    current=0 if count==0 else 1
                    layer,alpha,garment,plan,prev_gray=_new_take_layer(frame,takes[current],art)
                    reference_pixels=garment>128
                    light=float(np.mean(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)[reference_pixels]))
                    source_lab=cv2.cvtColor(frame,cv2.COLOR_BGR2LAB)
                    blank_shirt_lab=np.median(
                        source_lab[reference_pixels].astype(np.float32),axis=0
                    )
                    segments.append({
                        "take":current+1,"frameStart":count,
                        "placement":plan,"sourceBlankC1717":True,
                        "independentShirtAndCalibrationQA":"pending_review",
                    })
                    movement={"medianMotionPx":0.0,"p99MotionPx":0.0,"medianFabricResidual":0.0}
                else:
                    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
                    layer,alpha,garment,movement=_deform_from_previous(
                        prev_gray,gray,layer,alpha,garment,
                    )
                    prev_gray=gray
                    active_take = 0 if count < cut_frame else 1
                    movement_by_take[active_take].append(movement["medianMotionPx"])
                result=_blend_ink(
                    frame,layer,alpha,garment,light,blank_shirt_lab,
                )
                proc.stdin.write(result.tobytes())
                if count%max(1,int(fps))==0:
                    metrics.append({"frame":count,"second":round(count/fps,3),**movement})
                count+=1
            proc.stdin.close()
            error=proc.stderr.read(4000)
            status=proc.wait(timeout=30)
            capture.release()
            if status!=0 or count < int((cut_second+.3)*fps):
                raise HTTPException(502, "V4 pilot H264 encoding failed: "+error.decode(errors="replace")[:120])
            if not test_mode and count != reported_frames:
                raise HTTPException(422,"Truncated print source video")
            if any(sum(1 for step in motion if step > .20) < 2 or sum(motion) < .75
                   for motion in movement_by_take):
                raise HTTPException(422, f"{QA_HOLD}: no credible fabric movement across both takes")
            result=destination.read_bytes()
            if len(result) > MAX_SOURCE_MP4_BYTES:
                raise HTTPException(413,"Composited result exceeds Drive video cap")
            return result,{
                "contract":CONTRACT,
                "artworkSha256":master_sha,
                "plainVideoSha256":hashlib.sha256(plain_mp4).hexdigest(),
                "printedVideoSha256":hashlib.sha256(result).hexdigest(),
                "aspectRatio":aspect,
                "framesProcessed":count,"fps":round(fps,4),
                "durationSeconds":round(count/fps,3),
                "takeCutSecond":cut_second,"takes":segments,
                "tracking": "dense_backward_optical_flow_farneback_half_resolution",
                "artworkWarp": "per_frame_cotton_fabric_backward_remap",
                "matteInkLighting":"local_wrinkle_shading_and_global_cotton_light",
                "occlusionMatte":"shirt_color_difference_lab_conservative",
                "occlusionQa":"pending_independent_review_for_similar_color_occluders",
                "printIsNotRigidAlphaComposite":True,
                "motionStatistics":metrics,
                "manualFabricSurfaceQa":"pending_independent_visual_review",
                "notVerifiedAs3dClothTracking":True,
                "temporalQaNotPassed":True,
            }
        except (OSError, BrokenPipeError, subprocess.SubprocessError) as exc:
            raise HTTPException(502,"V4 compositor process failed") from exc
        finally:
            capture.release()
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
