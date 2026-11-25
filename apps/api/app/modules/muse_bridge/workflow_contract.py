"""CAM-authoritative artwork and dispatch contract, not a new render gate.

An artwork review compares design and placement against the reference photo.
The C1717 constraint is for the generated wearer/hero and output video only.
Never turn a failed review, local cache, or source metadata into approval.
"""
from __future__ import annotations

POLICY = "CAM_MUSE_SERVER_DRIVEN_WORKFLOW_V1"
QA_SCOPE = "CAM_MUSE_ARTWORK_QA_SCOPE_V2"
CHECKS = ("sourceImageReviewed", "designMatchesSource", "originalTextPreserved",
          "transparentBackgroundChecked", "placementChecked")


def review_scope():
    return {
        "policy": QA_SCOPE,
        "sourcePhotoRole": "design_comparison_only",
        "sourceGarmentMustBeC1717": False,
        "sourceGarmentMismatchAloneIsFailure": False,
        "requiredArtworkChecks": list(CHECKS),
        "minimumConfidence": 0.95,
        "outputGarment": "C1717",
        "outputGarmentValidationStage": "generated_hero_and_final_video",
        "instructions": (
            "Review the exact Original Native Master PNG against the source "
            "product photo for artwork fidelity and placement. Source garments "
            "may be tees, sweatshirts, hoodies or other apparel. A non-C1717 "
            "source garment alone is NOT an artwork failure. Never copy that "
            "garment or source photo as the video start frame. The generated "
            "adult wearer must wear a C1717 short-sleeve tee. Genuine artwork, "
            "placement, rights, provider and output QA holds remain in force."
        ),
    }


def artwork_qa(job):
    receipts = job.output_receipts if isinstance(job.output_receipts, dict) else {}
    review = receipts.get("visual_qa")
    matches = isinstance(review, dict) and bool(job.artwork_sha256) and (
        review.get("artwork_sha256") == job.artwork_sha256)
    if not isinstance(review, dict):
        status = "missing"
    elif not matches:
        status = "stale"
    elif review.get("verdict") == "fail":
        status = "fail"
    elif review.get("verdict") == "pass":
        status = "pass"
    else:
        status = "invalid"
    scope_recheck = receipts.get("sourceQaScopeRecheck")
    return {
        "authority": "CAM",
        "status": status,
        "sourceScopeRecheckState": scope_recheck.get("state") if isinstance(scope_recheck, dict) else None,
        "artworkSha256": job.artwork_sha256,
        "reviewRecordMatchesCurrentSha": bool(matches),
        "reviewedAtUtc": review.get("reviewed_at") if matches else None,
        "scopePolicy": QA_SCOPE,
        "statusPath": f"/api/v1/muse-bridge/jobs/{job.listing_key}/artwork-qa",
        "statusIsNotLeaseOrRendererPermission": True,
        "duplicateLocalReviewRequired": False,
    }


def execution(job, phase, headers):
    """Only construct from a server-selected eligible or just-claimed phase."""
    qa = artwork_qa(job)
    return {
        "version": POLICY, "nextAction": phase, "authority": "CAM",
        "artworkQa": qa,
        "freshArtworkReviewRequired": phase == "review_artwork",
        "sourceGarmentMustBeC1717": False,
        "outputGarment": "C1717",
        "originalArtworkRequired": True,
        "commandClaimHeaders": headers,
        "perListingClaimBodyAuthoritative": True,
        "neverRegenerateVerifiedRatios": True,
        "instruction": (
            "Follow the returned phase. For create_video, use the existing CAM "
            "QA record; missing local QA cache is not a blocker. For review_artwork "
            "read the artwork review contract; source garment is not an output QA gate. "
            "Use the exact videoJobClaim body and current headers. Do not infer "
            "additional proof requirements from legacy configuration. Claim is "
            "still checked by CAM. Respect real holds, cooldown and refusals."
        ),
    }


def config():
    return {
        "version": POLICY,
        "reviewScope": review_scope(),
        "qaStatusPathTemplate": "/api/v1/muse-bridge/jobs/{listingId}/artwork-qa",
        "discoveryPath": "/api/v1/muse-bridge/commands?limit=20",
        "perCommandField": "executionContract",
        "legacyReviewsEndpointIsPendingReviewQueue": True,
        "localQaCacheIsNotAuthority": True,
        "claimRevalidatesAllEligibility": True,
        "instructions": (
            "Use one /commands discovery feed, not a join with /reviews or "
            "/video-work. Treat the CAM-selected phase as authoritative for "
            "workflow routing, not as permission to bypass renderer or safety gates. "
            "A missing item in /reviews does not mean missing QA. Read artworkQa "
            "or its statusPath instead. Keep one active renderer; refresh after "
            "each completion; honor actual provider cooldown. Only render the "
            "missing ratios in the exact claimed job contract."
        ),
    }
