from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import (
    DeferredJobOutcome,
    JobHandlerContext,
    JobHandlerResult,
)
from app.domain.providers.contracts import AiProviderError
from app.domain.providers.registry import AiProviderUnavailableError
from app.infrastructure.downloader.secure_image import (
    DownloadLimitError,
    InvalidImageError,
    SecureDownloadError,
    UnsafeUrlError,
)
from app.modules.realistic_review_ugc.analysis import (
    ANALYZER_VERSION,
    ReferenceAnalysisDocument,
    analyze_reference_image,
    assess_ai_risk,
    build_ai_risk_calibration,
    build_reference_preference_model,
    confirm_ai_authenticity,
    evaluate_reference,
    hand_holding_hat_context_active,
    reference_preference_adjustment,
    reference_preference_features,
    policy_from_campaign,
    product_context_matching_active,
    should_confirm_ai_risk,
    strict_context_exclusions_active,
)
from app.modules.realistic_review_ugc.gemini_safety import (
    deferred_rrugc_ai_retry,
    scheduled_rrugc_gemini_slot,
)
from app.modules.realistic_review_ugc.keyword_strategy import campaign_learning_intent
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.seed_similarity import (
    SEED_VISUAL_MAX_PER_LABEL,
    SEED_VISUAL_PROFILE,
    SeedEmbeddingCache,
    SeedVisualAsset,
    compute_seed_visual_signal,
    high_confidence_negative_seed_gate,
)
from app.modules.realistic_review_ugc.service import (
    MIN_REFERENCE_PIXELS,
    MIN_REFERENCE_SHORT_EDGE,
    RrugcError,
    RrugcService,
    build_reference_downloader,
    download_reference_image,
    reference_resolution_usable,
)
from app.modules.realistic_review_ugc.visual_dedupe import (
    is_visual_near_duplicate,
    visual_fingerprints,
)


def _image_mime(image_format: str) -> str:
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "GIF": "image/gif",
        "TIFF": "image/tiff",
        "BMP": "image/bmp",
    }.get(image_format.upper(), "application/octet-stream")


def reference_qualification_resolution(
    *,
    manual_reference_good: bool,
    near_duplicate: bool,
    diversity_redundant: bool,
    decision_status: str,
    decision_reject_reason: str | None,
) -> tuple[str, str | None, str, str | None]:
    automatic_status = (
        "rejected_duplicate"
        if near_duplicate
        else "rejected_context"
        if diversity_redundant
        else decision_status
    )
    automatic_reject_reason = (
        "visual_near_duplicate"
        if near_duplicate
        else "DIVERSITY_REDUNDANT"
        if diversity_redundant
        else decision_reject_reason
    )
    if manual_reference_good:
        return "approved", None, automatic_status, automatic_reject_reason
    return (
        automatic_status,
        automatic_reject_reason,
        automatic_status,
        automatic_reject_reason,
    )


class RrugcCandidateAnalyzeJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self.seed_embedding_cache = SeedEmbeddingCache()

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        try:
            return asyncio.run(self._execute(context))
        except (UnsafeUrlError, DownloadLimitError, InvalidImageError) as exc:
            self._mark_error(context, exc.__class__.__name__, terminal=True)
            return JobHandlerResult.non_retryable(
                "rrugc_analysis_source_rejected",
                "Reference image is not safe or valid for analysis.",
            )
        except (SecureDownloadError, httpx.HTTPError) as exc:
            self._mark_error(context, exc.__class__.__name__, terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_analysis_source_unavailable",
                "Reference image could not be downloaded for analysis.",
            )
        except ValidationError as exc:
            context.logger.warning(
                "rrugc_candidate_analysis_invalid_document",
                extra={
                    "candidate_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "validation_errors": exc.errors(include_url=False),
                },
            )
            self._mark_error(context, "rrugc_analysis_invalid_document", terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_analysis_invalid_document",
                "Reference analyzer returned an invalid document.",
            )
        except AiProviderUnavailableError:
            self._mark_error(context, "ai_provider_unavailable", terminal=True)
            return JobHandlerResult.non_retryable(
                "ai_provider_unavailable",
                "Reference analyzer provider is unavailable.",
            )
        except AiProviderError as exc:
            self._mark_error(context, exc.code, terminal=not exc.retryable)
            deferred = deferred_rrugc_ai_retry(
                exc,
                message="Reference analyzer is temporarily unavailable.",
            )
            if deferred is not None:
                return deferred
            outcome = JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable
            return outcome(exc.code, "Reference analyzer failed.")
        except Exception:
            context.logger.exception(
                "rrugc_candidate_analysis_failed",
                extra={
                    "candidate_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "error_code": "rrugc_analysis_internal_error",
                },
            )
            self._mark_error(context, "rrugc_analysis_internal_error", terminal=False)
            return JobHandlerResult.retryable(
                "rrugc_analysis_internal_error",
                "Reference analysis failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        candidate_id = str(context.job.payload.get("candidate_id") or "")
        campaign_id = str(context.job.payload.get("campaign_id") or "")
        revision = context.job.payload.get("analysis_revision")
        if (
            not candidate_id
            or candidate_id != context.job.entity_id
            or not campaign_id
            or not isinstance(revision, int)
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_analysis_job_invalid",
                "Reference analysis job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            candidate = repository.get_candidate(
                context.job.tenant_id, campaign_id, candidate_id
            )
            campaign = repository.get_campaign(context.job.tenant_id, campaign_id)
            if candidate is None or campaign is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_candidate_not_found",
                    "Reference candidate was not found.",
                )
            if candidate.analysis_revision != revision:
                return JobHandlerResult.completed()
            signal = (
                candidate.ai_signal_json
                if isinstance(candidate.ai_signal_json, dict)
                else {}
            )
            manual_reference_good = signal.get("reference_manual_label") == "good"
            if candidate.status in {"import_queued", "importing", "drive_ready", "import_failed"}:
                return JobHandlerResult.completed()
            if candidate.status in {
                "approved",
                "needs_review",
                "rejected_duplicate",
                "rejected_no_person",
                "rejected_head_ratio",
                "rejected_expression",
                "rejected_existing_headwear",
                "rejected_head_occlusion",
                "rejected_quality",
                "rejected_ai_risk",
                "rejected_context",
            } and candidate.analyzed_at is not None:
                return JobHandlerResult.completed()
            if manual_reference_good:
                # Human REF qualification stays Approved while Gemini enriches
                # scores/metadata in the background.
                candidate.status = "approved"
            else:
                candidate.status = "analyzing"
            candidate.last_error_code = None
            session.commit()
            image_url = candidate.image_url
            policy = policy_from_campaign(campaign)
            manual_ai_label = candidate.ai_manual_label
            calibration = build_ai_risk_calibration(
                repository.ai_feedback_training_rows(context.job.tenant_id)
            )
            learning_intent = campaign_learning_intent(
                campaign_id=campaign.id,
                name=campaign.name,
                queries=list(campaign.search_queries_json or [campaign.query]),
                product_snapshot=campaign.product_snapshot_json,
            )
            reference_preference_model = build_reference_preference_model(
                repository.reference_feedback_training_rows(
                    context.job.tenant_id,
                    intent=learning_intent,
                    legacy_campaign_id=campaign.id,
                )
            )
            reference_preference_scope = "intent"
            product_context = dict(campaign.product_snapshot_json or {})
            if (
                campaign.discovery_mode == "product_context"
                and isinstance(campaign.product_context_json, dict)
            ):
                product_context["discovery_context"] = dict(
                    campaign.product_context_json
                )
            context_matching_required = product_context_matching_active(
                product_context
            )
            hand_holding_hat_context = hand_holding_hat_context_active(
                product_context
            )
            strict_context_exclusions = strict_context_exclusions_active(
                product_context
            )
            discovery_context = (
                dict(product_context.get("discovery_context"))
                if isinstance(product_context.get("discovery_context"), dict)
                else {}
            )
            visual_context = (
                dict(discovery_context.get("visual_context"))
                if isinstance(discovery_context.get("visual_context"), dict)
                else {}
            )
            context_binding_fingerprint = (
                str(visual_context.get("binding_fingerprint") or "").strip()
                if visual_context.get("status") == "ready"
                else ""
            ) or None
            product_variants = list(product_context.get("variants") or [])
            seed_visual_assets: list[SeedVisualAsset] = []
            seed_counts = {"positive": 0, "negative": 0}
            seed_source_counts: dict[str, dict[str, int]] = {
                "positive": {},
                "negative": {},
            }
            seed_hashes: set[str] = set()
            for seed in repository.list_reference_seeds(
                context.job.tenant_id,
                campaign.id,
                profile_key=SEED_VISUAL_PROFILE,
            ):
                if seed.label not in seed_counts:
                    continue
                if seed_counts[seed.label] >= SEED_VISUAL_MAX_PER_LABEL:
                    continue
                asset = repository.get_reference_asset(
                    context.job.tenant_id,
                    seed.reference_asset_id,
                )
                if (
                    asset is None
                    or asset.status != "ready"
                    or not asset.remote_file_id
                    or not asset.content_hash
                    or asset.content_hash in seed_hashes
                ):
                    continue
                seed_visual_assets.append(
                    SeedVisualAsset(
                        reference_asset_id=asset.id,
                        label=seed.label,
                        content_hash=asset.content_hash,
                        remote_file_id=asset.remote_file_id,
                        content_type=_image_mime(asset.image_format or ""),
                        size_bytes=asset.size_bytes,
                    )
                )
                seed_hashes.add(asset.content_hash)
                seed_counts[seed.label] += 1
                seed_source_counts[seed.label]["reference_library"] = (
                    seed_source_counts[seed.label].get("reference_library", 0) + 1
                )

            # Fill any unused seed slots from real outcomes without mutating the
            # user's Reference Library. Completed Stage 2 references are the
            # strongest implicit positive; explicit ref_good/ref_bad feedback
            # provides the remaining positive/negative examples.
            learned_visual_seeds = repository.visual_learning_seed_candidates(
                context.job.tenant_id,
                campaign.id,
                intent=learning_intent,
                profile_key=SEED_VISUAL_PROFILE,
                positive_limit=SEED_VISUAL_MAX_PER_LABEL * 2,
                negative_limit=SEED_VISUAL_MAX_PER_LABEL * 2,
            )
            for learned_label, learned_candidate, source_kind in learned_visual_seeds:
                if learned_label not in seed_counts:
                    continue
                if seed_counts[learned_label] >= SEED_VISUAL_MAX_PER_LABEL:
                    continue
                content_hash = str(learned_candidate.content_hash or "").strip()
                remote_file_id = str(learned_candidate.remote_file_id or "").strip()
                if not content_hash or not remote_file_id or content_hash in seed_hashes:
                    continue
                seed_visual_assets.append(
                    SeedVisualAsset(
                        reference_asset_id=learned_candidate.id,
                        label=learned_label,
                        content_hash=content_hash,
                        remote_file_id=remote_file_id,
                        content_type=_image_mime(
                            learned_candidate.image_format or ""
                        ),
                        size_bytes=learned_candidate.size_bytes,
                    )
                )
                seed_hashes.add(content_hash)
                seed_counts[learned_label] += 1
                seed_source_counts[learned_label][source_kind] = (
                    seed_source_counts[learned_label].get(source_kind, 0) + 1
                )

        downloader = build_reference_downloader()
        async with download_reference_image(image_url, downloader=downloader) as image:
            if context.is_cancelled:
                return JobHandlerResult.cancelled()
            if not reference_resolution_usable(image.width, image.height):
                with context.dependencies.session_factory() as session:
                    repository = RrugcRepository(session)
                    candidate = repository.get_candidate(
                        context.job.tenant_id, campaign_id, candidate_id
                    )
                    campaign = repository.get_campaign(
                        context.job.tenant_id, campaign_id
                    )
                    if candidate is None or campaign is None:
                        return JobHandlerResult.non_retryable(
                            "rrugc_candidate_not_found",
                            "Reference candidate was not found.",
                        )
                    if candidate.analysis_revision != revision:
                        return JobHandlerResult.completed()
                    signal = dict(candidate.ai_signal_json or {})
                    signal["resolution_gate"] = {
                        "usable": False,
                        "width": image.width,
                        "height": image.height,
                        "pixels": image.width * image.height,
                        "min_short_edge": MIN_REFERENCE_SHORT_EDGE,
                        "min_pixels": MIN_REFERENCE_PIXELS,
                    }
                    candidate.status = "rejected_quality"
                    candidate.reject_reason = "RESOLUTION_TOO_LOW"
                    candidate.image_url = image.source_url
                    candidate.content_hash = image.content_hash
                    candidate.width = image.width
                    candidate.height = image.height
                    candidate.size_bytes = image.size_bytes
                    candidate.image_format = image.image_format
                    candidate.quality_score = 0.0
                    candidate.final_score = 0.0
                    candidate.analyzer_provider = "technical_gate"
                    candidate.analyzer_model = None
                    candidate.analyzer_version = ANALYZER_VERSION
                    candidate.analysis_summary = (
                        f"Source resolution {image.width}x{image.height} is below "
                        f"the minimum usable reference threshold "
                        f"({MIN_REFERENCE_SHORT_EDGE}px short edge and "
                        f"{MIN_REFERENCE_PIXELS} pixels)."
                    )
                    candidate.analyzed_at = datetime.now(timezone.utc)
                    candidate.last_error_code = None
                    candidate.ai_signal_json = signal
                    RrugcService(session).refresh_campaign_completion(campaign)
                    session.commit()
                return JobHandlerResult.completed()

            image_bytes = image.path.read_bytes()
            fingerprints = visual_fingerprints(image_bytes)

            # Perceptual duplicate detection is a deterministic VPS-local gate.
            # Run it before Gemini so reposts/cropped copies of an already
            # approved reference do not consume model capacity. Keep human
            # REF approvals authoritative: those still continue to Gemini for
            # metadata enrichment.
            if not manual_reference_good and fingerprints:
                with context.dependencies.session_factory() as session:
                    repository = RrugcRepository(session)
                    candidate = repository.get_candidate(
                        context.job.tenant_id,
                        campaign_id,
                        candidate_id,
                    )
                    campaign = repository.get_campaign(
                        context.job.tenant_id,
                        campaign_id,
                    )
                    if candidate is None or campaign is None:
                        return JobHandlerResult.non_retryable(
                            "rrugc_candidate_not_found",
                            "Reference candidate was not found.",
                        )
                    if candidate.analysis_revision != revision:
                        return JobHandlerResult.completed()
                    signal = (
                        dict(candidate.ai_signal_json)
                        if isinstance(candidate.ai_signal_json, dict)
                        else {}
                    )
                    latest_manual_reference_good = (
                        signal.get("reference_manual_label") == "good"
                    )
                    if not latest_manual_reference_good:
                        existing_fingerprints = repository.visual_fingerprint_rows(
                            context.job.tenant_id,
                            candidate_id,
                        )
                        if is_visual_near_duplicate(
                            fingerprints,
                            existing_fingerprints,
                        ):
                            signal["visual_fingerprints"] = fingerprints
                            signal["local_prefilter"] = {
                                "gate": "visual_near_duplicate",
                                "runtime": "vps",
                                "provider_call_skipped": True,
                                "fingerprint_count": len(fingerprints),
                            }
                            candidate.status = "rejected_duplicate"
                            candidate.reject_reason = "visual_near_duplicate"
                            candidate.image_url = image.source_url
                            candidate.content_hash = image.content_hash
                            candidate.width = image.width
                            candidate.height = image.height
                            candidate.size_bytes = image.size_bytes
                            candidate.image_format = image.image_format
                            candidate.final_score = 0.0
                            candidate.analyzer_provider = "local_vps"
                            candidate.analyzer_model = "dhash16"
                            candidate.analyzer_version = ANALYZER_VERSION
                            candidate.analysis_summary = (
                                "Rejected locally before Gemini because this image "
                                "is a perceptual duplicate of an existing approved "
                                "reference."
                            )
                            candidate.analyzed_at = datetime.now(timezone.utc)
                            candidate.last_error_code = None
                            candidate.ai_signal_json = signal
                            service = RrugcService(session)
                            service.refresh_campaign_completion(campaign)
                            service.ensure_scout_backfill(campaign)
                            session.commit()
                            context.logger.info(
                                "rrugc_candidate_local_prefilter_rejected",
                                extra={
                                    "candidate_id": candidate_id,
                                    "tenant_id": context.job.tenant_id,
                                    "gate": "visual_near_duplicate",
                                    "gemini_skipped": True,
                                },
                            )
                            return JobHandlerResult.completed()

            # SigLIP2 seed similarity already runs on the VPS for ranking. Run it
            # before acquiring Gemini so a very high-confidence negative match
            # can finish locally. Weak/ambiguous/unavailable signals always fall
            # through to Gemini.
            seed_visual_signal = await compute_seed_visual_signal(
                tenant_id=context.job.tenant_id,
                candidate_bytes=image_bytes,
                seeds=seed_visual_assets,
                storage=context.dependencies.storage_provider,
                encoder_client=context.dependencies.resources.get(
                    "visual_encoder_client"
                ),
                cache=self.seed_embedding_cache,
                profile_key=SEED_VISUAL_PROFILE,
            )
            seed_visual_signal = {
                **seed_visual_signal,
                "selected_sources": {
                    label: dict(sorted(sources.items()))
                    for label, sources in seed_source_counts.items()
                    if sources
                },
            }
            seed_gate = high_confidence_negative_seed_gate(seed_visual_signal)
            if not manual_reference_good and seed_gate is not None:
                with context.dependencies.session_factory() as session:
                    repository = RrugcRepository(session)
                    candidate = repository.get_candidate(
                        context.job.tenant_id,
                        campaign_id,
                        candidate_id,
                    )
                    campaign = repository.get_campaign(
                        context.job.tenant_id,
                        campaign_id,
                    )
                    if candidate is None or campaign is None:
                        return JobHandlerResult.non_retryable(
                            "rrugc_candidate_not_found",
                            "Reference candidate was not found.",
                        )
                    if candidate.analysis_revision != revision:
                        return JobHandlerResult.completed()
                    signal = (
                        dict(candidate.ai_signal_json)
                        if isinstance(candidate.ai_signal_json, dict)
                        else {}
                    )
                    latest_manual_reference_good = (
                        signal.get("reference_manual_label") == "good"
                    )
                    if not latest_manual_reference_good:
                        local_prefilter = {
                            **seed_gate,
                            "runtime": "vps_siglip2",
                            "provider_call_skipped": True,
                        }
                        candidate.status = "rejected_context"
                        candidate.reject_reason = (
                            "SEED_VISUAL_HIGH_CONFIDENCE_NEGATIVE"
                        )
                        candidate.image_url = image.source_url
                        candidate.content_hash = image.content_hash
                        candidate.width = image.width
                        candidate.height = image.height
                        candidate.size_bytes = image.size_bytes
                        candidate.image_format = image.image_format
                        candidate.final_score = 0.0
                        candidate.analyzer_provider = "local_vps"
                        candidate.analyzer_model = "siglip2-seed-gate-v1"
                        candidate.analyzer_version = ANALYZER_VERSION
                        candidate.analysis_summary = (
                            "Rejected locally before Gemini because SigLIP2 "
                            "matched multiple user-trained negative references "
                            "with a high-confidence margin."
                        )
                        candidate.analyzed_at = datetime.now(timezone.utc)
                        candidate.last_error_code = None
                        candidate.ai_signal_json = {
                            **signal,
                            "visual_fingerprints": fingerprints,
                            "seed_visual": seed_visual_signal,
                            "local_prefilter": local_prefilter,
                        }
                        service = RrugcService(session)
                        service.refresh_campaign_completion(campaign)
                        service.ensure_scout_backfill(campaign)
                        session.commit()
                        context.logger.info(
                            "rrugc_candidate_local_prefilter_rejected",
                            extra={
                                "candidate_id": candidate_id,
                                "tenant_id": context.job.tenant_id,
                                "gate": seed_gate["gate"],
                                "gemini_skipped": True,
                                "negative_similarity": seed_gate[
                                    "negative_similarity"
                                ],
                                "positive_similarity": seed_gate[
                                    "positive_similarity"
                                ],
                                "margin": seed_gate["margin"],
                            },
                        )
                        return JobHandlerResult.completed()

            registry = context.dependencies.ai_provider_registry
            if registry is None:
                raise AiProviderUnavailableError("gemini")
            provider = registry.require("gemini")
            gemini_slot = scheduled_rrugc_gemini_slot(context.job)
            image_mime_type = _image_mime(image.image_format)
            document, provider_name, model = await analyze_reference_image(
                provider=provider,
                tenant_id=context.job.tenant_id,
                candidate_id=candidate_id,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
                width=image.width,
                height=image.height,
                product_context=product_context,
                preferred_model=(gemini_slot.model if gemini_slot else None),
                preferred_credential_provider=(
                    gemini_slot.credential_provider if gemini_slot else None
                ),
            )
            variant_by_id = {
                str(item.get("id") or ""): item
                for item in product_variants
                if str(item.get("id") or "")
            }
            if not document.existing_headwear:
                document.matched_variant_id = None
                document.matched_variant_name = None
                document.matched_color = None
                document.color_match_score = 0.0
            elif document.matched_variant_id not in variant_by_id:
                document.matched_variant_id = None
                document.matched_variant_name = None
                document.color_match_score = 0.0
            else:
                matched_variant = variant_by_id[document.matched_variant_id]
                document.matched_variant_name = str(
                    matched_variant.get("name")
                    or matched_variant.get("color")
                    or ""
                ).strip() or None
                document.matched_color = str(
                    matched_variant.get("color")
                    or document.matched_color
                    or ""
                ).strip() or None
            confirmation = None
            if manual_ai_label not in {"real", "ai"} and should_confirm_ai_risk(
                document, policy
            ):
                confirmation = await confirm_ai_authenticity(
                    provider=provider,
                    tenant_id=context.job.tenant_id,
                    candidate_id=candidate_id,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                    width=image.width,
                    height=image.height,
                    preferred_model=(gemini_slot.model if gemini_slot else None),
                    preferred_credential_provider=(
                        gemini_slot.credential_provider if gemini_slot else None
                    ),
                )
            ai_assessment = assess_ai_risk(
                document,
                confirmation=confirmation,
                calibration=calibration,
            )
            preference_features = reference_preference_features(
                phone_authenticity_score=document.phone_authenticity_score,
                mobile_ugc_score=document.mobile_ugc_score,
                product_fit_score=document.product_fit_score,
                quality_score=document.quality_score,
                artistic_editorial_risk=document.artistic_editorial_risk,
                ai_risk_score=ai_assessment.calibrated_score,
            )
            preference_adjustment = reference_preference_adjustment(
                preference_features,
                reference_preference_model,
            )
            decision = evaluate_reference(
                document,
                policy,
                effective_ai_risk_score=ai_assessment.calibrated_score,
                ai_evidence_count=ai_assessment.strong_evidence_count,
                ai_detector_confidence=ai_assessment.detector_confidence,
                ai_risk_confirmed=ai_assessment.confirmed,
                manual_ai_label=manual_ai_label,
                reference_preference_score=preference_adjustment,
                variant_matching_required=bool(product_variants),
                context_matching_required=context_matching_required,
                allow_hand_held_hat=hand_holding_hat_context,
                strict_context_exclusions=strict_context_exclusions,
            )

            with context.dependencies.session_factory() as session:
                repository = RrugcRepository(session)
                candidate = repository.get_candidate(
                    context.job.tenant_id, campaign_id, candidate_id
                )
                campaign = repository.get_campaign(context.job.tenant_id, campaign_id)
                if candidate is None or campaign is None:
                    return JobHandlerResult.non_retryable(
                        "rrugc_candidate_not_found",
                        "Reference candidate was not found.",
                    )
                if candidate.analysis_revision != revision:
                    return JobHandlerResult.completed()

                signal = (
                    candidate.ai_signal_json
                    if isinstance(candidate.ai_signal_json, dict)
                    else {}
                )
                manual_reference_good = (
                    signal.get("reference_manual_label") == "good"
                )
                manual_reference_override = manual_reference_good
                eligible_for_approval = decision.approved or manual_reference_override

                existing_fingerprints = repository.visual_fingerprint_rows(
                    context.job.tenant_id,
                    candidate_id,
                )
                near_duplicate = eligible_for_approval and is_visual_near_duplicate(
                    fingerprints,
                    existing_fingerprints,
                )
                diversity_signature = self._diversity_signature(document)
                similar_compositions = (
                    repository.campaign_diversity_signature_count(
                        context.job.tenant_id,
                        campaign_id,
                        candidate_id,
                        diversity_signature,
                    )
                    if diversity_signature
                    else 0
                )
                diversity_redundant = (
                    eligible_for_approval
                    and not near_duplicate
                    and diversity_signature is not None
                    and similar_compositions >= 3
                )
                (
                    status,
                    reject_reason,
                    automatic_status,
                    automatic_reject_reason,
                ) = reference_qualification_resolution(
                    manual_reference_good=manual_reference_good,
                    near_duplicate=near_duplicate,
                    diversity_redundant=diversity_redundant,
                    decision_status=decision.status,
                    decision_reject_reason=decision.reject_reason,
                )
                self._apply_document(
                    candidate,
                    document,
                    ai_assessment,
                    status,
                    reject_reason,
                    decision.final_score,
                    provider_name,
                    model,
                    image.content_hash,
                    image.width,
                    image.height,
                    image.size_bytes,
                    image.image_format,
                    fingerprints,
                    diversity_signature,
                    context_matching_required,
                    context_binding_fingerprint,
                )
                candidate.image_url = image.source_url
                repository.replace_visual_fingerprints(candidate, fingerprints)
                candidate.ai_signal_json = {
                    **(candidate.ai_signal_json or {}),
                    "reference_preference_adjustment": preference_adjustment,
                    "seed_visual": seed_visual_signal,
                    "reference_manual_pending_approval": False,
                    "reference_manual_pending_analysis": False,
                    "reference_manual_approval_override": manual_reference_override,
                    "reference_manual_auto_status": (
                        automatic_status if manual_reference_good else None
                    ),
                    "reference_manual_auto_reject_reason": (
                        automatic_reject_reason if manual_reference_good else None
                    ),
                    "reference_preference_model": {
                        "active": reference_preference_model.active,
                        "good_count": reference_preference_model.good_count,
                        "bad_count": reference_preference_model.bad_count,
                        "learning_intent": learning_intent,
                        "scope": reference_preference_scope,
                    },
                }
                service = RrugcService(session)
                if (
                    status == "approved"
                    and campaign.auto_import
                ):
                    service.enqueue_import(candidate)
                service.refresh_campaign_completion(campaign)
                service.ensure_scout_backfill(campaign)
                session.commit()

        context.logger.info(
            "rrugc_candidate_analysis_completed"
            + " status="
            + str(status)
            + " reject_reason="
            + str(reject_reason or "-")
            + " final_score="
            + str(round(float(decision.final_score), 4)),
            extra={
                "candidate_id": candidate_id,
                "tenant_id": context.job.tenant_id,
                "status": status,
                "final_score": decision.final_score,
                "reject_reason": reject_reason,
                "analyzer_version": ANALYZER_VERSION,
            },
        )
        return JobHandlerResult.completed()

    @staticmethod
    def _apply_document(
        candidate,
        document: ReferenceAnalysisDocument,
        ai_assessment,
        status: str,
        reject_reason: str | None,
        final_score: float,
        provider_name: str,
        model: str | None,
        content_hash: str,
        width: int,
        height: int,
        size_bytes: int,
        image_format: str,
        fingerprints: list[str],
        diversity_signature: str | None,
        context_matching_required: bool,
        context_binding_fingerprint: str | None,
    ) -> None:
        candidate.status = status
        candidate.people_count = document.people_count
        candidate.primary_head_ratio = document.primary_head_ratio
        candidate.smile_score = document.smile_score
        candidate.head_visible = document.head_visible
        candidate.existing_headwear = document.existing_headwear
        candidate.head_occlusion = document.head_occlusion
        candidate.mobile_ugc_score = document.mobile_ugc_score
        candidate.phone_authenticity_score = document.phone_authenticity_score
        candidate.artistic_editorial_risk = document.artistic_editorial_risk
        candidate.quality_score = document.quality_score
        candidate.ai_risk_score = ai_assessment.calibrated_score
        candidate.ai_risk_raw_score = ai_assessment.raw_score
        candidate.ai_detector_confidence = ai_assessment.detector_confidence
        candidate.ai_risk_confirmed = ai_assessment.confirmed
        provenance = {
            key: value
            for key, value in (candidate.ai_signal_json or {}).items()
            if (
                key == "scout_query"
                or key.startswith("reference_manual_")
                or key.startswith("context_manual_")
            )
        }
        candidate.diversity_signature = diversity_signature
        candidate.ai_signal_json = {
            **provenance,
            **(ai_assessment.signal_json or {}),
            "visual_fingerprints": fingerprints,
            "diversity_signature": diversity_signature,
            "diversity": {
                "scene_type": document.scene_type,
                "framing_type": document.framing_type,
                "camera_angle": document.camera_angle,
                "pose_type": document.pose_type,
            },
            "context_match": {
                "active": context_matching_required,
                "score": document.context_match_score,
                "evidence": list(document.context_match_evidence),
                "binding_fingerprint": context_binding_fingerprint,
            },
            "composition": {
                "hand_visible": document.hand_visible,
                "hat_held_in_hand": document.hat_held_in_hand,
                "front_panel_visible": document.front_panel_visible,
                "embroidery_visible": document.embroidery_visible,
            },
        }
        candidate.product_fit_score = document.product_fit_score
        candidate.matched_variant_id = document.matched_variant_id
        candidate.matched_variant_name = document.matched_variant_name
        candidate.matched_color = document.matched_color
        candidate.color_match_score = document.color_match_score
        candidate.product_shape_score = document.product_shape_score
        candidate.final_score = final_score
        candidate.reject_reason = reject_reason
        candidate.analyzer_provider = provider_name
        candidate.analyzer_model = model
        candidate.analyzer_version = ANALYZER_VERSION
        candidate.analysis_summary = document.summary
        candidate.analyzed_at = datetime.now(timezone.utc)
        candidate.last_error_code = None
        candidate.content_hash = content_hash
        candidate.width = width
        candidate.height = height
        candidate.size_bytes = size_bytes
        candidate.image_format = image_format

    @staticmethod
    def _diversity_signature(document: ReferenceAnalysisDocument) -> str | None:
        values = (
            document.scene_type,
            document.framing_type,
            document.camera_angle,
            document.pose_type,
        )
        normalized = [
            str(value).strip().lower().replace(" ", "_") for value in values
        ]
        if any(not value or value in {"unknown", "other"} for value in normalized):
            return None
        return "|".join(normalized)

    @staticmethod
    def _mark_error(context: JobHandlerContext, code: str, *, terminal: bool) -> None:
        candidate_id = str(context.job.payload.get("candidate_id") or "")
        campaign_id = str(context.job.payload.get("campaign_id") or "")
        revision = context.job.payload.get("analysis_revision")
        if not candidate_id or not campaign_id:
            return
        with context.dependencies.session_factory() as session:
            candidate = RrugcRepository(session).get_candidate(
                context.job.tenant_id, campaign_id, candidate_id
            )
            if candidate is None or candidate.analysis_revision != revision:
                return
            signal = (
                candidate.ai_signal_json
                if isinstance(candidate.ai_signal_json, dict)
                else {}
            )
            manual_reference_good = signal.get("reference_manual_label") == "good"
            if manual_reference_good and candidate.status not in {
                "import_queued",
                "importing",
                "drive_ready",
                "import_failed",
            }:
                # Gemini enrichment failures must not undo a human REF approval.
                candidate.status = "approved"
                signal["reference_manual_pending_analysis"] = True
                candidate.ai_signal_json = signal
            elif terminal:
                # A terminal analysis failure frees this slot so auto Scout can
                # backfill it with another candidate.
                candidate.status = "analysis_failed"
            else:
                # Processing will retry this exact job. Keep it counted as
                # in-flight so Scout does not overfill the target while the
                # retry is pending.
                candidate.status = "analysis_queued"
            candidate.last_error_code = code[:100]

            if terminal:
                campaign = RrugcRepository(session).get_campaign(
                    context.job.tenant_id,
                    campaign_id,
                )
                if campaign is not None:
                    RrugcService(session).ensure_scout_backfill(campaign)
            session.commit()


class RrugcCandidateImportJobHandler:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult:
        try:
            return asyncio.run(self._execute(context))
        except RrugcError as exc:
            outcome = JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable
            return outcome(exc.code, str(exc))
        except Exception:
            context.logger.exception(
                "rrugc_candidate_import_failed",
                extra={
                    "candidate_id": context.job.entity_id,
                    "tenant_id": context.job.tenant_id,
                    "error_code": "rrugc_import_internal_error",
                },
            )
            return JobHandlerResult.retryable(
                "rrugc_import_internal_error",
                "Reference import failed.",
            )

    async def _execute(self, context: JobHandlerContext) -> JobHandlerResult:
        candidate_id = str(context.job.payload.get("candidate_id") or "")
        campaign_id = str(context.job.payload.get("campaign_id") or "")
        revision = context.job.payload.get("import_revision")
        if (
            not candidate_id
            or candidate_id != context.job.entity_id
            or not campaign_id
            or not isinstance(revision, int)
        ):
            return JobHandlerResult.non_retryable(
                "rrugc_import_job_invalid",
                "Reference import job payload is invalid.",
            )
        if context.is_cancelled:
            return JobHandlerResult.cancelled()
        storage = context.dependencies.storage_provider
        if storage is None:
            return JobHandlerResult.retryable(
                "managed_storage_unavailable",
                "Managed storage is unavailable.",
            )

        with context.dependencies.session_factory() as session:
            repository = RrugcRepository(session)
            candidate = repository.get_candidate(
                context.job.tenant_id, campaign_id, candidate_id
            )
            if candidate is None:
                return JobHandlerResult.non_retryable(
                    "rrugc_candidate_not_found",
                    "Reference candidate was not found.",
                )
            if candidate.import_revision != revision:
                return JobHandlerResult.completed()
            if candidate.status == "rejected_duplicate":
                return JobHandlerResult.completed()
            await RrugcService(session).import_candidate(
                candidate=candidate,
                storage=storage,
            )

        context.logger.info(
            "rrugc_candidate_import_completed",
            extra={
                "candidate_id": candidate_id,
                "tenant_id": context.job.tenant_id,
            },
        )
        return JobHandlerResult.completed()
