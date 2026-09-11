from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

from .paths import RuntimePaths

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
DEFAULT_STATE_ROOT = "/var/lib/dola-render-gateway"
DEFAULT_RUNTIME_ROOT = "/run/dola-render-gateway"
_PLACEHOLDERS = {"changeme", "change-me", "secret", "test", "example"}


class RuntimeConfigurationError(ValueError):
    pass


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _internal_key(value: str | None) -> str:
    key = str(value or "").strip()
    if not key:
        raise RuntimeConfigurationError("DOLA_INTERNAL_API_KEY is required.")
    if key.lower() in _PLACEHOLDERS:
        raise RuntimeConfigurationError("DOLA_INTERNAL_API_KEY must not use a placeholder value.")
    return key


@dataclass(frozen=True)
class RuntimeSettings:
    host: str
    port: int
    allow_non_loopback: bool
    internal_api_key: str
    paths: RuntimePaths

    @property
    def redacted(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "allow_non_loopback": self.allow_non_loopback,
            "state_root": str(self.paths.state_root),
            "runtime_root": str(self.paths.runtime_root),
            "internal_api_key_configured": True,
        }


def load_settings(
    environ: dict[str, str] | None = None,
    *,
    source_root: Path | None = None,
) -> RuntimeSettings:
    values = os.environ if environ is None else environ
    host = str(values.get("DOLA_GATEWAY_HOST", DEFAULT_HOST)).strip()
    if not host:
        raise RuntimeConfigurationError("DOLA_GATEWAY_HOST must not be empty.")
    allow_non_loopback = _truthy(values.get("DOLA_ALLOW_NON_LOOPBACK"))
    if not _loopback(host) and not allow_non_loopback:
        raise RuntimeConfigurationError(
            "DOLA_GATEWAY_HOST must be loopback unless DOLA_ALLOW_NON_LOOPBACK=true."
        )
    try:
        port = int(str(values.get("DOLA_GATEWAY_PORT", DEFAULT_PORT)).strip())
    except ValueError as exc:
        raise RuntimeConfigurationError("DOLA_GATEWAY_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeConfigurationError("DOLA_GATEWAY_PORT must be between 1 and 65535.")
    source = source_root or Path(__file__).resolve().parents[1] / "upstream"
    paths = RuntimePaths.from_roots(
        source_root=source,
        state_root=str(values.get("DOLA_STATE_ROOT", DEFAULT_STATE_ROOT)),
        runtime_root=str(values.get("DOLA_RUNTIME_ROOT", DEFAULT_RUNTIME_ROOT)),
    )
    return RuntimeSettings(
        host=host,
        port=port,
        allow_non_loopback=allow_non_loopback,
        internal_api_key=_internal_key(values.get("DOLA_INTERNAL_API_KEY")),
        paths=paths,
    )
