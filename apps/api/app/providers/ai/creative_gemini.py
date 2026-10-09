from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.providers.contracts import AiBatchResult, AiBatchResultsInput, AiBatchStatus, AiBatchStatusInput, AiBatchSubmission, AiBatchSubmissionInput, AiMetadataAnalysisInput, AiMetadataAnalysisResult, AiProviderError
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository
from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository
from app.modules.ai_operations.gemini_failover import backup_is_active
from app.modules.ai_operations.credential_model import CreativeAiCredentialModel
from app.modules.ai_operations.credentials import (
    CreativeAiCredentialRepository,
    CreativeCredentialError,
    CreativeGeminiCredential,
    CreativeGeminiCredentialResolver,
    creative_credential_cipher,
)
from app.providers.ai.gemini import GeminiAiMetadataProvider, GeminiModelUnavailable

_PACIFIC_TIME = ZoneInfo("America/Los_Angeles")
_LOGGER = logging.getLogger("cam.providers.creative_gemini")
_GEMINI_PERMISSION_COOLDOWN = timedelta(hours=1)


class GeminiCredentialPermissionDeferred(AiProviderError):
    """A credential/project-level 403: rotate keys without losing RRUGC jobs."""

    def __init__(self, retry_at: datetime):
        super().__init__(
            "Gemini credential project access was denied. Waiting for an available credential.",
            code="gemini_credential_permission_denied",
            retryable=True,
            status_code=403,
        )
        self.earliest_retry_at = retry_at


class _CredentialQuotaCoordinator:
    def __init__(self, session_factory: Callable[[], Session], quota_scope: str, project_rpd: int):
        self.session_factory, self.quota_scope, self.project_rpd = session_factory, quota_scope, project_rpd

    def reserve_request(self, *, model: str, rpd: int, now: datetime):
        with self.session_factory() as session:
            decision = GeminiProjectQuotaRepository(session).reserve_request(quota_scope=self.quota_scope, model=model, rpd=rpd, project_rpd=self.project_rpd, now=now)
            session.commit()
        if decision.allowed:
            return None
        return GeminiModelUnavailable(model=model, reason=decision.reason or "rpd_exhausted", available_at=decision.available_at or now)

    def block_until(self, *, model: str, retry_at: datetime) -> None:
        with self.session_factory() as session:
            GeminiProjectQuotaRepository(session).block_until(quota_scope=self.quota_scope, model=model, retry_at=retry_at)
            session.commit()


@dataclass
class _CachedProvider:
    provider: GeminiAiMetadataProvider
    expires_at: float


class RuntimeCreativeGeminiProvider:
    """Runtime tenant credential boundary for Creative Gemini.

    A delegate is created per key fingerprint so a rotated credential never
    reuses an old HTTP client or in-memory cooldown. Durable Gemini quotas are
    deliberately keyed by the configured Google-project scope instead: Gemini
    applies RPD per project, not per API key.
    """
    provider_name = "gemini"
    supports_single = True
    supports_batch = True
    batch_max_items = 100
    batch_max_request_bytes = 20_000_000

    def __init__(self, settings: Settings, session_factory: Callable[[], Session], *, provider_factory: Callable[..., GeminiAiMetadataProvider] = GeminiAiMetadataProvider, cache_ttl_seconds: float = 300.0, cache_size: int = 8):
        self.settings, self.session_factory = settings, session_factory
        self.default_model = settings.GEMINI_MODEL
        self._resolver = CreativeGeminiCredentialResolver(session_factory, settings)
        self._provider_factory, self._cache_ttl, self._cache_size = provider_factory, cache_ttl_seconds, cache_size
        self._providers: OrderedDict[str, _CachedProvider] = OrderedDict()

    def _credential(self, tenant_id: str, *, preferred_provider: str | None = None) -> CreativeGeminiCredential:
        try:
            with self.session_factory() as session:
                use_backup = backup_is_active(session, self.settings, tenant_id)
                active_backups = CreativeAiCredentialRepository(
                    session, None
                ).list_active_backup_providers(tenant_id)
            if preferred_provider == "gemini" or (
                preferred_provider is not None
                and preferred_provider.startswith("gemini_backup_")
            ):
                selected_provider = preferred_provider
            elif use_backup and active_backups:
                selected_provider = active_backups[0]
            else:
                selected_provider = "gemini"
            try:
                credential = self._resolver.resolve(tenant_id, provider=selected_provider)
            except CreativeCredentialError:
                if selected_provider.startswith("gemini_backup_") and preferred_provider is not None and preferred_provider.startswith("gemini_backup_"):
                    raise
                credential = self._resolver.resolve(tenant_id)
        except CreativeCredentialError as exc:
            raise AiProviderError("Creative Gemini credential is unavailable.", code=exc.code, retryable=False, status_code=503) from exc
        scope = self.settings.GEMINI_PROJECT_QUOTA_SCOPE
        _LOGGER.info("creative_gemini_credential_resolved tenant_id=%s credential_source=%s credential_fingerprint_prefix=%s effective_quota_scope=%s model=%s", tenant_id, credential.source, credential.fingerprint[:12], scope, self.default_model)
        return credential

    def _delegate_for(self, tenant_id: str, credential: CreativeGeminiCredential) -> GeminiAiMetadataProvider:
        now = time.monotonic()
        cached = self._providers.pop(credential.fingerprint, None)
        if cached is not None and cached.expires_at > now:
            self._providers[credential.fingerprint] = cached
            return cached.provider
        scope = f"{self.settings.GEMINI_PROJECT_QUOTA_SCOPE}:{tenant_id}:{credential.fingerprint}"
        coordinator = _CredentialQuotaCoordinator(self.session_factory, scope, self.settings.gemini_project_daily_request_limit)
        provider = self._provider_factory(credential.secret, model=self.settings.GEMINI_MODEL, timeout_seconds=self.settings.GEMINI_TIMEOUT_SECONDS, model_pool=self.settings.gemini_model_pool, model_limits=self.settings.gemini_model_limits, cooldown_seconds=self.settings.GEMINI_MODEL_COOLDOWN_SECONDS, quota_coordinator=coordinator)
        self._providers[credential.fingerprint] = _CachedProvider(provider, now + self._cache_ttl)
        while len(self._providers) > self._cache_size:
            self._providers.popitem(last=False)
        return provider

    def _batch_credential(self, tenant_id: str, encrypted_secret: str | None, key_version: str | None, fingerprint: str | None) -> CreativeGeminiCredential:
        if encrypted_secret and key_version and fingerprint:
            try:
                secret = creative_credential_cipher(self.settings).decrypt(
                    encrypted_secret,
                    key_version=key_version,
                    aad=f"creative-ai-credential:{tenant_id}:gemini",
                )
            except Exception as exc:
                raise AiProviderError("The credential for this Gemini batch is unavailable.", code="creative_gemini_batch_credential_unavailable", retryable=False, status_code=503) from exc
            if not secret or hashlib.sha256(secret.encode()).hexdigest() != fingerprint:
                raise AiProviderError("The credential for this Gemini batch is unavailable.", code="creative_gemini_batch_credential_unavailable", retryable=False, status_code=503)
            return CreativeGeminiCredential(secret, fingerprint, "batch_affinity", secret[-4:], encrypted_secret, key_version)
        current = self._credential(tenant_id)
        if fingerprint and current.fingerprint != fingerprint:
            raise AiProviderError("The credential for this Gemini batch has changed.", code="creative_gemini_batch_credential_rotated", retryable=False, status_code=409)
        return current

    def _quarantine_denied_credential(
        self, *, tenant_id: str, fingerprint: str,
    ) -> datetime:
        """Persist an isolated cooldown across all workers and RRUGC model gates.

        The Google project denial is credential-specific, not model-specific.
        Preserve the key (no secret deletion) and only quarantine matching
        fingerprints; other projects may continue processing immediately.
        """
        from app.modules.processing_policy.claim import RRUGC_GEMINI_MODEL_GATE_PREFIX

        now = datetime.now(timezone.utc)
        retry_at = now + _GEMINI_PERMISSION_COOLDOWN
        with self.session_factory() as session:
            rows = session.scalars(
                select(CreativeAiCredentialModel).where(
                    CreativeAiCredentialModel.tenant_id == tenant_id,
                    CreativeAiCredentialModel.status == "active",
                    CreativeAiCredentialModel.secret_fingerprint == fingerprint,
                )
            ).all()
            providers: set[str] = set()
            for row in rows:
                if row.provider == "gemini" or row.provider.startswith("gemini_backup_"):
                    providers.add(row.provider)
                    row.last_test_status = "PERMISSION_DENIED"
                    row.last_tested_at = now
            env_key = (self.settings.GEMINI_API_KEY or "").strip()
            if env_key and hashlib.sha256(env_key.encode()).hexdigest() == fingerprint:
                providers.add("gemini")
            if not providers:
                # A key may have been rotated after this request was started;
                # never block a different fingerprint's newly installed key.
                _LOGGER.warning("gemini_permission_denied_rotated tenant_id=%s", tenant_id)
                return retry_at

            limiter = AiModelRateLimitRepository(session)
            models = set(self.settings.gemini_model_pool)
            models.add(self.settings.GEMINI_MODEL)
            for provider in providers:
                for model in models:
                    for scope in (provider, RRUGC_GEMINI_MODEL_GATE_PREFIX + provider):
                        limiter.block_until(
                            tenant_id=tenant_id, provider=scope, model=model,
                            retry_at=retry_at, now=now,
                        )
            session.commit()
        _LOGGER.warning(
            "gemini_credential_project_denied tenant_id=%s providers=%s cooldown_seconds=%s",
            tenant_id, ",".join(sorted(providers)),
            int(_GEMINI_PERMISSION_COOLDOWN.total_seconds()),
        )
        return retry_at

    async def analyze_single(self, input: AiMetadataAnalysisInput) -> AiMetadataAnalysisResult:
        credential = self._credential(input.tenant_id, preferred_provider=input.preferred_credential_provider)
        try:
            return await self._delegate_for(input.tenant_id, credential).analyze_single(input)
        except AiProviderError as exc:
            if (
                exc.status_code == 403
                and exc.details.get("google_error_status") == "PERMISSION_DENIED"
            ):
                self._quarantine_denied_credential(
                    tenant_id=input.tenant_id, fingerprint=credential.fingerprint,
                )
                # RRUGC deferred outcomes retain the original processing job
                # while other healthy keys are considered at the next claim.
                raise GeminiCredentialPermissionDeferred(
                    datetime.now(timezone.utc) + timedelta(seconds=30)
                ) from exc
            raise

    def _batch_affinity(self, tenant_id: str, credential: CreativeGeminiCredential) -> tuple[str | None, str | None]:
        if credential.encrypted_secret and credential.key_version:
            return credential.encrypted_secret, credential.key_version
        try:
            encrypted = creative_credential_cipher(self.settings).encrypt(
                credential.secret,
                aad=f"creative-ai-credential:{tenant_id}:gemini",
            )
        except CreativeCredentialError:
            # Existing env-only deployments remain usable; an affinity-less batch
            # fails closed if the active key changes before it is completed.
            return None, None
        assert encrypted is not None
        return encrypted.ciphertext, encrypted.key_version

    async def submit_batch(self, input: AiBatchSubmissionInput) -> AiBatchSubmission:
        credential = self._credential(input.tenant_id)
        result = await self._delegate_for(input.tenant_id, credential).submit_batch(input)
        encrypted_secret, key_version = self._batch_affinity(input.tenant_id, credential)
        return replace(
            result,
            credential_fingerprint=credential.fingerprint,
            credential_encrypted_secret=encrypted_secret,
            credential_key_version=key_version,
        )

    async def get_batch_status(self, input: AiBatchStatusInput) -> AiBatchStatus:
        return await self._delegate_for(input.tenant_id, self._batch_credential(input.tenant_id, input.credential_encrypted_secret, input.credential_key_version, input.credential_fingerprint)).get_batch_status(input)

    async def stream_batch_results(self, input: AiBatchResultsInput) -> AsyncIterator[AiBatchResult]:
        async for result in self._delegate_for(input.tenant_id, self._batch_credential(input.tenant_id, input.credential_encrypted_secret, input.credential_key_version, input.credential_fingerprint)).stream_batch_results(input):
            yield result

    async def cancel_batch(self, input: AiBatchStatusInput) -> bool:
        return await self._delegate_for(input.tenant_id, self._batch_credential(input.tenant_id, input.credential_encrypted_secret, input.credential_key_version, input.credential_fingerprint)).cancel_batch(input)

    async def aclose(self) -> None:
        providers = tuple(entry.provider for entry in self._providers.values())
        self._providers.clear()
        for provider in providers:
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                await closer()
