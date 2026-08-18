from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from math import floor
from app.core.config import Settings
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository
from app.modules.video_search.analysis import estimate_video_input_tokens
from app.providers.ai.gemini_video import MEDIA_RESOLUTION_LOW

@dataclass(frozen=True, slots=True)
class VideoModelSelection:
    model: str; safe_tpm: int; estimated_input_tokens: int; automatic_rpd: int; automatic_project_rpd: int | None; quota_scope: str
@dataclass(frozen=True, slots=True)
class VideoQuotaDeferral:
    retry_at: datetime; reasons: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class VideoNoSafeModel:
    reasons: tuple[str, ...]

class VideoFreeTierModelPlanner:
    def __init__(self, settings: Settings, quota: GeminiProjectQuotaRepository, *, quota_scope: str):
        self.settings, self.quota, self.quota_scope = settings, quota, quota_scope
    def select(self, *, duration_ms: int, now: datetime | None = None):
        estimate=estimate_video_input_tokens(duration_ms=duration_ms, media_resolution=MEDIA_RESOLUTION_LOW)
        reasons=[]; retry=[]; compatible=False
        project=floor(self.settings.gemini_project_daily_request_limit*self.settings.VIDEO_AI_DAILY_BUDGET_RATIO)
        for model in self.settings.gemini_model_pool:
            limit=self.settings.gemini_model_limits[model]; safe=floor(limit.tpm*self.settings.VIDEO_AI_TOKEN_SAFETY_RATIO); daily=floor(limit.rpd*self.settings.VIDEO_AI_DAILY_BUDGET_RATIO)
            if estimate.total_tokens > safe: reasons.append(f"{model}:token_budget_exceeded"); continue
            compatible=True
            if daily <= 0 or project <= 0: reasons.append(f"{model}:automatic_daily_budget_disabled"); continue
            decision=self.quota.check_request_availability(quota_scope=self.quota_scope,model=model,rpd=daily,project_rpd=project,now=now)
            if decision.allowed: return VideoModelSelection(model,safe,estimate.total_tokens,daily,project,self.quota_scope)
            reasons.append(f"{model}:{decision.reason}")
            if decision.available_at: retry.append(decision.available_at)
        return VideoQuotaDeferral(min(retry),tuple(reasons)) if compatible and retry else VideoNoSafeModel(tuple(reasons))

    def select_pinned(self, *, model: str, duration_ms: int, now: datetime | None = None):
        """Validate a persisted run model without silently switching models."""
        if model not in self.settings.gemini_model_pool or model not in self.settings.gemini_model_limits:
            return VideoNoSafeModel((f"{model}:not_explicitly_configured",))
        estimate = estimate_video_input_tokens(duration_ms=duration_ms, media_resolution=MEDIA_RESOLUTION_LOW)
        limit = self.settings.gemini_model_limits[model]
        safe = floor(limit.tpm * self.settings.VIDEO_AI_TOKEN_SAFETY_RATIO)
        daily = floor(limit.rpd * self.settings.VIDEO_AI_DAILY_BUDGET_RATIO)
        project = floor(self.settings.gemini_project_daily_request_limit * self.settings.VIDEO_AI_DAILY_BUDGET_RATIO)
        if estimate.total_tokens > safe:
            return VideoNoSafeModel((f"{model}:token_budget_exceeded",))
        if daily <= 0 or project <= 0:
            return VideoNoSafeModel((f"{model}:automatic_daily_budget_disabled",))
        decision = self.quota.check_request_availability(quota_scope=self.quota_scope, model=model, rpd=daily, project_rpd=project, now=now)
        if not decision.allowed:
            if decision.available_at:
                return VideoQuotaDeferral(decision.available_at, (f"{model}:{decision.reason}",))
            return VideoNoSafeModel((f"{model}:{decision.reason}",))
        return VideoModelSelection(model, safe, estimate.total_tokens, daily, project, self.quota_scope)

    def reserve(self, selection: VideoModelSelection, *, now: datetime | None = None):
        return self.quota.reserve_request(quota_scope=selection.quota_scope,model=selection.model,rpd=selection.automatic_rpd,project_rpd=selection.automatic_project_rpd,now=now)
