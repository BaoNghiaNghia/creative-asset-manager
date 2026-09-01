from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from app.modules.image_generation.providers import DeferredGenerationResult, GeneratedImageResult, PreparedImage, ProviderPollResult
from app.modules.image_generation.safe_download import SafeImageDownloadError, download_validated_image

IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
FIREFLY_BASE_URL = "https://firefly-api.adobe.io"
FIREFLY_OAUTH_SCOPE = "openid,AdobeID,firefly_api"
FIREFLY_RESULT_HOSTS = ("firefly-api.adobe.io", "firefly.adobe.com", "adobe.io")


class FireflyProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    expires_at: float


def _retry_after(response: httpx.Response, default: int = 10) -> int:
    value = response.headers.get("retry-after", "").strip()
    if value.isdigit():
        return max(1, min(60, int(value)))
    if value:
        try:
            seconds = int((parsedate_to_datetime(value) - parsedate_to_datetime(response.headers["date"])).total_seconds())
            return max(1, min(60, seconds))
        except Exception:
            pass
    return default


def _validated_adobe_url(url: object) -> str:
    if not isinstance(url, str):
        raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid URL.")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid URL.")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in FIREFLY_RESULT_HOSTS):
        raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an untrusted URL.")
    return url


class AdobeFireflySquareProvider:
    """Strict Firefly canvas expansion; never falls back to another provider."""

    provider_key = "adobe_firefly"
    preservation_mode = "strict_expand"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        oauth_scope: str = FIREFLY_OAUTH_SCOPE,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.oauth_scope = oauth_scope.strip() or FIREFLY_OAUTH_SCOPE
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
                        "scope": self.oauth_scope,
                    },
                )
            except httpx.HTTPError as exc:
                raise FireflyProviderError("firefly_provider_unavailable", "Adobe Firefly authentication is unavailable.", retryable=True) from exc
            if response.status_code in {401, 403}:
                raise FireflyProviderError("firefly_auth_failed", "Adobe Firefly credentials were rejected.")
            if response.status_code == 429 or response.status_code >= 500:
                raise FireflyProviderError("firefly_provider_unavailable", "Adobe Firefly authentication is temporarily unavailable.", retryable=True)
            if response.is_error:
                raise FireflyProviderError("firefly_auth_failed", "Adobe Firefly authentication failed.")
            try:
                payload = response.json()
                token = payload["access_token"]
                expires_in = float(payload.get("expires_in", 0))
            except (KeyError, TypeError, ValueError):
                raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid authentication response.")
            if not isinstance(token, str) or not token:
                raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid authentication response.")
            self._token = _Token(token, time.monotonic() + max(0, expires_in))
            return token

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}", "x-api-key": self.client_id}

    @staticmethod
    def _raise_response_error(response: httpx.Response, operation: str) -> None:
        if response.status_code == 429:
            raise FireflyProviderError("firefly_rate_limited", "Adobe Firefly rate limit reached.", retryable=True)
        if response.status_code >= 500:
            raise FireflyProviderError("firefly_provider_unavailable", "Adobe Firefly is temporarily unavailable.", retryable=True)
        if response.is_error:
            raise FireflyProviderError(f"firefly_{operation}_failed", f"Adobe Firefly {operation} failed.")

    async def generate_square(self, *, source: PreparedImage, target_size: int, prompt: str | None) -> DeferredGenerationResult:
        if target_size not in (1024, 2048):
            raise FireflyProviderError("image_generation_target_size_unsupported", "Unsupported square target size.")
        headers = await self._headers()
        try:
            upload = await self._client.post(
                f"{FIREFLY_BASE_URL}/v2/storage/image",
                headers={**headers, "Content-Type": source.mime_type},
                content=source.image_bytes,
            )
        except httpx.HTTPError as exc:
            raise FireflyProviderError("firefly_provider_unavailable", "Adobe Firefly upload is unavailable.", retryable=True) from exc
        self._raise_response_error(upload, "upload")
        try:
            upload_id = upload.json()["images"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid upload response.")
        if not isinstance(upload_id, str) or not upload_id:
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid upload response.")

        payload: dict[str, object] = {
            "image": {"source": {"uploadId": upload_id}},
            "size": {"width": target_size, "height": target_size},
            "numVariations": 1,
        }
        if prompt and prompt.strip():
            payload["prompt"] = prompt.strip()
        try:
            submit = await self._client.post(
                f"{FIREFLY_BASE_URL}/v3/images/expand-async",
                headers={**headers, "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise FireflyProviderError(
                "firefly_submission_uncertain",
                "Adobe Firefly submission outcome is unknown.",
                uncertain=True,
            ) from exc
        if submit.status_code >= 500:
            raise FireflyProviderError(
                "firefly_submission_uncertain",
                "Adobe Firefly submission outcome is unknown.",
                uncertain=True,
            )
        self._raise_response_error(submit, "submission")
        try:
            result = submit.json()
            job_id = result["jobId"]
            status_url = _validated_adobe_url(result["statusUrl"])
            cancel_value = result.get("cancelUrl")
            cancel_url = _validated_adobe_url(cancel_value) if cancel_value else None
        except (ValueError, KeyError, TypeError):
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid job reference.")
        if not isinstance(job_id, str) or not job_id:
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid job reference.")
        return DeferredGenerationResult(
            provider="adobe_firefly",
            provider_job_id=job_id,
            status_url=status_url,
            cancel_url=cancel_url,
            upload_id=upload_id,
        )

    async def poll(self, *, status_url: str) -> ProviderPollResult:
        url = _validated_adobe_url(status_url)
        try:
            response = await self._client.get(url, headers=await self._headers())
        except httpx.HTTPError as exc:
            raise FireflyProviderError("firefly_provider_unavailable", "Adobe Firefly status is unavailable.", retryable=True) from exc
        self._raise_response_error(response, "poll")
        try:
            payload = response.json()
            raw_status = str(payload["status"]).lower()
        except (ValueError, KeyError, TypeError):
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an invalid status response.")
        if raw_status in {"pending", "queued", "submitted", "running", "in_progress"}:
            return ProviderPollResult(state="running", retry_after_seconds=_retry_after(response))
        if raw_status in {"failed", "error"}:
            return ProviderPollResult(state="failed", error_code="firefly_generation_failed")
        if raw_status in {"cancelled", "canceled"}:
            return ProviderPollResult(state="cancelled")
        if raw_status not in {"succeeded", "completed", "done"}:
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned an unknown status.")
        try:
            output_url = payload["result"]["images"][0]["url"]
        except (KeyError, IndexError, TypeError):
            raise FireflyProviderError("firefly_invalid_response", "Adobe Firefly returned no generated image.")
        return ProviderPollResult(state="succeeded", output_url=_validated_adobe_url(output_url))

    async def download_result(self, *, output_url: str, target_size: int) -> GeneratedImageResult:
        try:
            data, mime = await download_validated_image(
                self._client,
                output_url,
                allowed_hosts=FIREFLY_RESULT_HOSTS,
                target_size=target_size,
            )
        except SafeImageDownloadError as exc:
            raise FireflyProviderError(exc.code, str(exc), retryable=exc.retryable) from exc
        return GeneratedImageResult(provider="adobe_firefly", model=None, image_bytes=data, mime_type=mime)

    async def cancel(self, *, cancel_url: str) -> None:
        url = _validated_adobe_url(cancel_url)
        try:
            response = await self._client.post(url, headers=await self._headers())
        except httpx.HTTPError as exc:
            raise FireflyProviderError("firefly_provider_unavailable", "Adobe Firefly cancellation is unavailable.", retryable=True) from exc
        if response.status_code in {404, 409, 410}:
            return
        self._raise_response_error(response, "cancellation")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
