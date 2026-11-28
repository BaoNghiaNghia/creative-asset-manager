"""Internal Native-first candidate policy; not a Muse wire contract.

Only trusted CAM services may invoke these guards with verified inputs.
No changes to live flags, provider calls, storage or video.
"""
from dataclasses import dataclass
from . import policy as p

NATIVE = "NATIVE_IMAGEGEN"
HYBRID = "HYBRID_COMPOSITOR"
METHODS = (NATIVE, HYBRID)
CHECKS = frozenset(("artworkFidelityAndReadability", "garmentAndInkContrast",
                    "roleFraming", "placementVisual", "printScaleConsistency",
                    "noMajorDefects"))
FIDELITY_FAILURES = frozenset(("FAIL_ARTWORK_REDRAWN", "FAIL_ARTWORK_UNREADABLE",
                              "FAIL_INK_COLOR_SHIFT", "FAIL_TEXTURE_DRIFT",
                              "FAIL_ALPHA_EDGE_SPILL", "FAIL_WHITE_INK_LOSS",
                              "FAIL_SOURCE_SUBSTRATE_BAKED"))


@dataclass(frozen=True)
class Profile:
    prompt_sha: str
    minimum_px: int = p.PUBLICATION_MIN_NATIVE
    visual_qa: bool = False
    hybrid_fallback: bool = False
    operator_receipt_sha: str | None = None

    def __post_init__(self):
        p.require(p.valid_sha(self.prompt_sha) and type(self.minimum_px) is int
                  and self.minimum_px in (1600, 2000)
                  and type(self.visual_qa) is bool and type(self.hybrid_fallback) is bool,
                  "native_first_policy_invalid")
        if self.minimum_px == 1600 or self.visual_qa or self.hybrid_fallback:
            p.require(p.valid_sha(self.operator_receipt_sha),
                      "native_first_operator_policy_required")


PROFILE_FIELD = "nativeFirst"


def profile_from_plan(plan):
    """Internal immutable CAM profile, not an asserted Muse wire schema."""
    if not isinstance(plan, dict) or PROFILE_FIELD not in plan:
        return None
    v=plan[PROFILE_FIELD]
    p.require(isinstance(v,dict) and set(v)=={
        "promptTemplateSha256","minimumPublicationPx","visualEstimateQa",
        "fallbackAllowed","operatorPolicyReceiptSha256"}, "native_first_policy_invalid")
    profile=Profile(v["promptTemplateSha256"],v["minimumPublicationPx"],
                    v["visualEstimateQa"],v["fallbackAllowed"],v["operatorPolicyReceiptSha256"])
    # Native-only for the first live adapter: Hybrid P2 remains preview-only
    # until persistent method history and a production compositor are bound.
    p.require(profile.visual_qa and not profile.hybrid_fallback,
              "native_first_live_profile_unverified")
    return profile


def next_method(profile, role, history, *, hybrid_adapter_verified=False):
    """Use only the trusted reconciled attempt history; one shared budget."""
    p.require(type(role) is int and 1 <= role <= 13, "invalid_role")
    p.require(isinstance(history, (tuple,list)) and len(history) <= p.MAX_ROLE_ATTEMPTS,
              "native_first_history_invalid")
    ids = set()
    for i, a in enumerate(history):
        p.require(isinstance(a, dict) and set(a) == {"attemptId","method","state","failure"},
                  "native_first_history_invalid")
        aid = a["attemptId"]
        p.require(isinstance(aid,str) and p._ID.fullmatch(aid) and aid not in ids
                  and a["method"] in METHODS, "native_first_history_invalid")
        ids.add(aid)
        p.require(a["state"] == "confirmed_failure", "reconcile_or_preserve_existing_role")
        p.require(isinstance(a["failure"],str) and p._ID.fullmatch(a["failure"]),
                  "native_first_history_invalid")
        if a["method"] == HYBRID:
            p.require(role == 1 and profile.hybrid_fallback and i > 0
                      and history[i-1]["method"] == NATIVE
                      and history[i-1]["failure"] in FIDELITY_FAILURES,
                      "hybrid_history_invalid")
    hybrids = sum(a["method"] == HYBRID for a in history)
    p.require(hybrids <= 1, "hybrid_attempt_limit")
    p.require(len(history) < p.MAX_ROLE_ATTEMPTS, "image_role_attempt_limit")
    if (history and role == 1 and profile.hybrid_fallback and hybrid_adapter_verified is True
            and hybrids == 0 and history[-1]["failure"] in FIDELITY_FAILURES):
        return HYBRID
    return NATIVE


def assess(profile, identity, *, role, epoch, attempt_id, plan_sha, method,
           image_sha, reviewed_sha, width, height, native_verified, upscaled,
           checks, reviewer, worker, evidence_sha, prompt_sha,
           physical_calibration=False):
    """Return internal storage eligibility, never a gallery authorization.

    A trusted automatic reviewer must inspect the exact output and source.
    Caller authenticates reviewer, verifies PNG bytes and origin, and resolves
    the immutable plan/attempt from CAM. Six check names are internal pending
    a mapping to the actual Muse V5 schema. Placement is a visual target,
    not measured calibration; macro/infographic do not require a visible collar.
    """
    p.require(profile.visual_qa is True, "native_visual_policy_not_approved")
    p.require(isinstance(identity,p.PackIdentity) and type(role) is int and 1<=role<=13
              and type(epoch) is int and epoch>=1 and isinstance(attempt_id,str)
              and p._ID.fullmatch(attempt_id) and method in METHODS,
              "native_qa_attempt_invalid")
    p.require(all(p.valid_sha(x) for x in (plan_sha,image_sha,evidence_sha))
              and image_sha == reviewed_sha and prompt_sha == profile.prompt_sha,
              "native_qa_identity_mismatch")
    p.require(type(width) is int and type(height) is int and width == height
              and 1600 <= width <= 4000, "native_dimensions_invalid")
    p.require(native_verified is True and upscaled is False and physical_calibration is False,
              "native_origin_or_calibration_invalid")
    p.require(all(isinstance(x,str) and p._ID.fullmatch(x) for x in (reviewer,worker))
              and reviewer != worker, "qa_self_approval_denied")
    p.require(isinstance(checks,dict) and set(checks)==CHECKS
              and all(type(x) is bool for x in checks.values()), "native_qa_checks_invalid")
    passed=all(checks.values())
    eligible=passed and method==NATIVE and width>=profile.minimum_px
    record={"policy":"CAM_IMAGE_NATIVE_FIRST_V5_CANDIDATE",
            "packId":identity.pack_id,"tenantId":identity.tenant,"role":role,
            "epoch":epoch,"attemptId":attempt_id,"planSha256":plan_sha,
            "artworkSha256":identity.artwork_sha,"sourceSha256":identity.source_sha,
            "placementLockSha256":identity.placement_sha,"skillSha256":identity.skill_sha,
            "generationMethod":method,"promptTemplateSha256":prompt_sha,
            "imageSha256":image_sha,"width":width,"height":height,
            "reviewedBy":reviewer,"workerId":worker,"reviewEvidenceSha256":evidence_sha,
            "operatorPolicyReceiptSha256":profile.operator_receipt_sha,
            "checks":dict(checks),"minimumPublicationPx":profile.minimum_px,
            "qaPass":passed,"nativeOriginVerified":True,"upscaled":False,
            "physicalCalibrationVerified":False,"previewOnly":not eligible,
            "eligibleForStorageAdmission":eligible,"publicationAuthorized":False}
    return {**record,"qaRecordSha256":p.canonical_sha(record)}
