"""Private Cloudflare R2 S3-compatible adapter; no bucket listing or public URLs."""
import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings


def _load_botocore_types():
    try:
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise RuntimeError(
            "botocore is required for R2 error handling"
        ) from exc
    return Config, BotoCoreError, ClientError


def _load_boto_sdk():
    """Load the optional-at-import-time R2 SDK only when R2 is actually used."""
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3/botocore are required when the R2 video cache is enabled"
        ) from exc
    Config, BotoCoreError, ClientError = _load_botocore_types()
    return boto3, Config, BotoCoreError, ClientError


class R2ProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(f"R2 operation failed ({code})")


class R2NotFound(R2ProviderError):
    def __init__(self):
        super().__init__("not_found")


@dataclass(frozen=True)
class R2ObjectHead:
    size_bytes: int
    etag: str | None


class R2Adapter:
    def __init__(self, settings: Settings, *, client: Any | None = None):
        if not settings.R2_VIDEO_CACHE_ENABLED:
            raise ValueError("R2 video cache is disabled")
        self.bucket = settings.R2_BUCKET_NAME
        if client is not None:
            self._client = client
            return
        boto3, Config, _BotoCoreError, _ClientError = _load_boto_sdk()
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            region_name="auto",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID.get_secret_value(),
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY.get_secret_value(),
            config=Config(
                s3={"addressing_style": "path"}, retries={"max_attempts": 3},
                connect_timeout=10, read_timeout=60,
            ),
        )

    @staticmethod
    def _raise_safe(exc: Exception) -> None:
        try:
            _Config, BotoCoreError, ClientError = _load_botocore_types()
        except RuntimeError:
            raise exc
        if isinstance(exc, ClientError):
            response = exc.response
            raw_code = str(response.get("Error", {}).get("Code", ""))
            status = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
            if raw_code in {"404", "NoSuchKey", "NotFound"} or status == 404:
                raise R2NotFound() from None
            retryable = status in {408, 429} or status >= 500 or raw_code in {
                "SlowDown", "RequestTimeout", "ServiceUnavailable", "InternalError"
            }
            raise R2ProviderError("transient" if retryable else "permanent", retryable=retryable) from None
        if isinstance(exc, BotoCoreError):
            raise R2ProviderError("transport", retryable=True) from None
        raise exc

    async def _call(self, method: str, **kwargs):
        task = asyncio.create_task(
            asyncio.to_thread(getattr(self._client, method), Bucket=self.bucket, **kwargs)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)  # Finish in-flight part before caller aborts upload.
            except Exception:
                pass
            raise
        except Exception as exc:
            self._raise_safe(exc)

    async def head_object(self, key: str) -> R2ObjectHead:
        result = await self._call("head_object", Key=key)
        return R2ObjectHead(size_bytes=int(result["ContentLength"]), etag=result.get("ETag"))

    async def object_exists(self, key: str) -> bool:
        try:
            await self.head_object(key)
            return True
        except R2NotFound:
            return False

    async def delete_object(self, key: str) -> None:
        try:
            await self._call("delete_object", Key=key)
        except R2NotFound:
            pass

    async def create_multipart_upload(self, key: str, mime_type: str) -> str:
        result = await self._call("create_multipart_upload", Key=key, ContentType=mime_type)
        return str(result["UploadId"])

    async def upload_part(self, key: str, upload_id: str, part_number: int, body: bytes) -> str:
        result = await self._call(
            "upload_part", Key=key, UploadId=upload_id, PartNumber=part_number, Body=body
        )
        return str(result["ETag"])

    async def complete_multipart_upload(self, key: str, upload_id: str, parts: list[dict]) -> str | None:
        result = await self._call(
            "complete_multipart_upload", Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return result.get("ETag")

    async def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        try:
            await self._call("abort_multipart_upload", Key=key, UploadId=upload_id)
        except R2NotFound:
            pass
