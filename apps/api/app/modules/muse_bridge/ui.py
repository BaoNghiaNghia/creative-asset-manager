"""Tenant-scoped Etsy Spy Video UGC projection from existing CamMuseJob rows."""
from __future__ import annotations


def video_ugc_view(job) -> dict:
    """Never expose Muse tokens, Drive internal IDs or unverified receipt URLs."""
    if job is None:
        return {"status": "not_queued", "count": 0, "videos": []}
    r = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    uploads = r.get("drive_uploads")
    checked = (uploads if isinstance(uploads, dict) else {})
    videos = []
    review = r.get("independentVideoReviewV33")
    review = review if isinstance(review, dict) else {}
    for ratio, slug in (("9:16", "vertical"), ("16:9", "horizontal")):
        item = checked.get(ratio)
        if (not isinstance(item, dict)
                or item.get("storageVerified") is not True
                or item.get("storage_provider") != "google_drive_managed"
                or not item.get("drive_file_id")
                or not item.get("drive_folder_id")):
            continue
        qa = "pending_review"
        if (review.get("artworkSha256") == job.artwork_sha256
                and isinstance(item.get("sha256"), str)
                and (review.get("reviewedVideoSha256ByAspect") or {}).get(ratio) == item["sha256"]
                and review.get("verdict") in ("pass", "hold")):
            qa = review["verdict"]
        videos.append({
            "aspectRatio": ratio,
            "url": f"/api/v1/etsy-spy/video-ugc/{job.listing_key}/{slug}",
            "posterUrl": f"/api/v1/etsy-spy/video-ugc/{job.listing_key}/{slug}/poster/640",
            "thumbnailUrl": f"/api/v1/etsy-spy/video-ugc/{job.listing_key}/{slug}/poster/320",
            "width": item.get("width"),
            "height": item.get("height"),
            "durationSeconds": item.get("durationSeconds"),
            "qaStatus": qa,
            "storageVerified": True,
            "creativePolicy": item.get("creativePolicy"),
        })
    if videos:
        state = "saved" if len(videos) == 2 else "partial"
    elif job.status == "qa_hold":
        state = "qa_hold"
    elif job.status == "missing_source":
        state = "missing_source"
    elif job.status == "claimed":
        state = "rendering"
    elif job.status == "pending":
        state = "queued"
    elif job.status == "submitted":
        state = "receipt_only"
    else:
        state = job.status
    return {
        "status": state, "count": len(videos),
        "videos": videos,
        "contentQa": (("hold" if any(v["qaStatus"] == "hold" for v in videos)
                       else "pass" if all(v["qaStatus"] == "pass" for v in videos)
                       else "pending_review") if videos else None),
        "camLinked": bool(len(videos) == 2 and r.get("camLinked")),
        "missingAspectRatios": [ratio for ratio in ("9:16", "16:9")
                                if ratio not in {v["aspectRatio"] for v in videos}],
        "artworkSha256": job.artwork_sha256,
    }
