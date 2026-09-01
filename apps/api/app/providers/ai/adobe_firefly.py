from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.modules.image_generation.providers import DeferredGenerationResult, PreparedImage

IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
FIREFLY_BASE_URL = "https://firefly-api.adobe.io"


class FireflyProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    expires_at: float


class AdobeFireflySquareProvider:
    """Strict canvas expansion provider; never falls back to another provider."""

    provider_key = "adobe_firefly"
    preservation_mode = "strict_expand"

    def __init__(self, *, client_id: str, client_secret: str, http_client: httpx.AsyncClient | None = None):
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        self._owns_client = http_client is None
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _access_token(self) -> str:
        if not self.available:
            raise FireflyProviderError("firefly_not_configured", "Adobe Firefly is not configured.")
        cached = self._token
        if cached and cached.expires_at > time.monotonic() + 60:
            return cached.value
        async with self._token_lock:
            cached = self._token
            if cached and cached.expires_at > time.monotonic() + 60:
                return cached.value
            try:
                response = await self._client.post(
                    IMS_TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "client_credentials",
                        "scope": "openid,AdobeID,firefly_api",
                    },
                )
            except httpx.HTTPError as exc:
                raise FireflyProviderError("firefly_auth_transport_error", "Adobe Firefly authentication could not be reached.", retryable=True) from exc
            if response.status_code in {401, 403}:
                raise FireflyProviderError("firefly_auth_failed", "Adobe Firefly credentials were rejected.")
            if response.status_code >= 500:
                raise FireflyProviderError("firefly_auth_unavailable", "Adobe Firefly authentication is temporarily unavailable.", retryable=True)
            if response.is_error:
                raise FireflyProviderError("firefly_auth_failed", "Adobe Firefly authentication failed.")
            payload = response.json()
            token = payload.get("access_token")
            expires_in = payload.get("expires_in", 0)
            if not isinstance(token, str) or not token or not isinstance(expires_in, (int, float)):
                raise FireflyProviderError("firefly_auth_invalid_response", "Adobe Firefly returned an invalid authentication response.")
            self._token = _Token(token, time.monotonic() + max(0, float(expires_in)))
            return token

    async def generate_square(self, *, source: PreparedImage, target_size: int, prompt: str | None) -> DeferredGenerationResult:
        if target_size not in (1024, 2048):
            raise FireflyProviderError("image_generation_target_size_unsupported", "Unsupported square target size.")
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}", "x-api-key": self.client_id}
        files = {"file": ("source." + source.mime_type.split("/")[-1], source.image_bytes, source.mime_type)}
        try:
            upload = await self._client.post(f"{FIREFLY_BASE_URL}/v2/storage/image", headers=headers, files=files)
        except httpx.HTTPError as exc:
            raise FireflyProviderError("firefly_upload_transport_error", "Adobe Firefly upload could not be reached.", retryable=True) from exc
        if upload.status_code == 429:
            raise FireflyProviderError("firefly_rate_limited", "Adobe Firefly rate limit reached.", retryable=True)
        if upload.status_code >= 500:
            raise FireflyProviderError("firefly_upload_unavailable", "Adobe Firefly upload is temporarily unavailable.", retryable=True)
        if upload.is_error:
            raise FireflyProviderError("firefly_upload_failed", "Adobe Firefly rejected the source image.")
        body = upload.json()
        upload_id = body.get("id") or body.get("uploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise FireflyProviderError("firefly_upload_invalid_response", "Adobe Firefly upload response is invalid.")
        payload = {"image": {"id": upload_id}, "size": {"width": target_size, "height": target_size}, "numVariations": 1}
        if prompt and prompt.strip():
            payload["prompt"] = prompt.strip()
        try:
            submit = await self._client.post(f"{FIREFLY_BASE_URL}/v3/images/expand-async", headers={**headers, "Content-Type": "application/json"}, json=payload)
        except httpx.HTTPError as exc:
            raise FireflyProviderError("firefly_submission_uncertain", "Adobe Firefly submission outcome is unknown.") from exc
        if submit.status_code == 429:
            raise FireflyProviderError("firefly_rate_limited", "Adobe Firefly rate limit reached.", retryable=True)
        if submit.status_code >= 500:
            raise FireflyProviderError("firefly_submission_uncertain", "Adobe Firefly submission outcome is unknown.")
        if submit.is_error:
            raise FireflyProviderError("firefly_submission_failed", "Adobe Firefly rejected the generation request.")
        result = submit.json()
        job_id = result.get("jobId") or result.get("id")
        status_url = result.get("statusUrl") or result.get("status_url")
        cancel_url = result.get("cancelUrl") or result.get("cancel_url")
        if not isinstance(job_id, str) or not isinstance(status_url, str) or not status_url.startswith("https://firefly-api.adobe.io/"):
            raise FireflyProviderError("firefly_submission_invalid_response", "Adobe Firefly returned an invalid job reference.")
        return DeferredGenerationResult(provider="adobe_firefly", provider_job_id=job_id, status_url=status_url, cancel_url=cancel_url if isinstance(cancel_url, str) else None, upload_id=upload_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
