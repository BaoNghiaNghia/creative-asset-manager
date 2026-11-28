"""Shared runner guidance; no extra leases, renderer calls, or QA gates."""
from typing import Literal

POLICY = "CAM_MUSE_EXECUTION_OPTIMIZATION_V1"
FailureCategory = Literal[
    "listing_visual_qa", "source_framing", "media_integrity_refused",
    "provider_transient", "cam_transport", "storage", "lease_or_contract",
    "unknown",
]


def config():
    return {
        "version": POLICY,
        "scope": "video_only_new_work_and_safe_runner_checkpoints",
        "failureClassification": {
            "completionField": "failureCategory",
            "listingVisualQa": "listing_visual_qa",
            "sourceFraming": "source_framing",
            "mediaIntegrityRefusal": "media_integrity_refused",
            "camTransport": "cam_transport",
            "storage": "storage",
            "providerTransient": "provider_transient",
            "leaseOrContract": "lease_or_contract",
            "unknown": "unknown",
            "providerCircuitStatuses": [502, 503, 504],
            "providerCircuitStage": "generate_video",
            "providerCircuitPauseSeconds": 300,
            "providerCircuitDistinctListings": 2,
            "providerCircuitWindowSeconds": 600,
            "visualQaOpensProviderCircuit": False,
            "preserveProvider429Cooldown": True,
            "instructions": (
                "Use the optional structured failureCategory in failed command completion. "
                "Only actual media.generate_video HTTP 502/503/504 errors use "
                "provider_transient plus rendererHttpStatus and rendererErrorStage. "
                "Do not count source or visual QA, media integrity refusal, CAM HTTP "
                "errors or upload errors toward the provider circuit. Finish the "
                "failed listing within the existing correction budget; honor hard holds "
                "and operator instructions. Continue another eligible listing once "
                "the command is closed; do not retry an integrity-refused operation. "
                "A genuine provider 429 still uses the existing cooldown/yield protocol."
            ),
        },
        "continuousTransition": {
            "singleRunLock": True,
            "maxActiveRendererOperations": 1,
            "nextJobDelaySeconds": [2, 5],
            "targetUploadToNextEligibleClaimSeconds": 15,
            "idlePollSeconds": 120,
            "preserveExistingInvocationBudget": True,
            "instructions": (
                "After a verified upload or terminal listing-specific outcome, "
                "complete the command, reconcile any lost ACK, and proceed after "
                "the 2-5 second transition to fresh config and discovery while "
                "eligible work and the existing run budget remain. Do not wait "
                "for the next scheduler occurrence or sleep 300s for visual QA. "
                "Never claim another job while the prior renderer or lease remains "
                "unresolved. Respect actual server order, retry delays, cooldown, "
                "failover, circuit state, single-run lock and invocation timeout."
            ),
        },
        "heartbeat": {
            "version": "CAM_MUSE_HEARTBEAT_SYNC_V1",
            "workerHeartbeatSeconds": 60,
            "commandHeartbeatSecondsAdvisory": 60,
            "runIdScope": "one_actual_invocation",
            "renderAttemptIdScope": "one_actual_renderer_call",
            "renderAttemptCorrelationFields": [
                "runId", "renderAttemptId", "commandId", "listingId", "aspectRatio"
            ],
            "workerStates": ["idle", "polling", "reviewing", "rendering",
                             "uploading", "cooldown", "blocked", "stopping"],
            "commandStates": ["started", "researching", "collecting",
                              "validating", "uploading", "blocked"],
            "instructions": (
                "Emit real stage events at checkpoints, including HERO_READY and "
                "RENDER_STARTED/outcome. Preserve one runId for the invocation "
                "and one renderAttemptId with identical commandId, listingId and "
                "aspectRatio across each actual renderer start and outcome. "
                "Worker telemetry heartbeat is not lease renewal. Send honest "
                "heartbeat around every 60s when supported, including actual "
                "render polling; use reviewing for source/visual checks. For an "
                "owned active command send its exact command lease to the existing "
                "heartbeat endpoint, using collecting while rendering and "
                "validating for QA. Never extend a dead/expired lease or start a "
                "standalone heartbeat process. On blocking calls use an in-run "
                "scoped heartbeat only if the runtime supports it. Use bounded "
                "outbox flushes and do not delay business work for telemetry."
            ),
        },
        "safeFramingPolicyVersion": "CAM_VIDEO_SAFE_FRAMING_V1",
        "preserveSavedMedia": True,
        "preserveActiveLeases": True,
        "additionalServerQaGate": False,
        "runnerAdoptionRequiresReadback": True,
    }
