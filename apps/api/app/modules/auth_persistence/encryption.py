from __future__ import annotations
import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class TokenEncryptionError(RuntimeError):
    pass

@dataclass(frozen=True)
class EncryptedValue:
    ciphertext: str
    key_version: str

class TokenCipher:
    """Versioned AES-256-GCM token encryption; keys are never persisted."""

    def __init__(self, keys: dict[str, bytes], active_version: str):
        if active_version not in keys:
            raise ValueError("active encryption key version is unavailable")
        if not keys or any(len(key) != 32 for key in keys.values()):
            raise ValueError("OAuth encryption keys must be 32 bytes")
        self._keys = dict(keys)
        self.active_version = active_version

    @classmethod
    def from_config(cls, value: str, active_version: str):
        keys = {}
        for part in value.split(","):
            if not part.strip():
                continue
            try:
                version, encoded = part.strip().split(":", 1)
                key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, binascii.Error) as exc:
                raise ValueError("invalid OAuth encryption key configuration") from exc
            if not version or version in keys:
                raise ValueError("duplicate or empty OAuth encryption key version")
            keys[version] = key
        return cls(keys, active_version)

    def encrypt(self, plaintext: str | None, *, aad: str) -> EncryptedValue | None:
        if plaintext is None:
            return None
        nonce = os.urandom(12)
        encrypted = AESGCM(self._keys[self.active_version]).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
        return EncryptedValue(payload, self.active_version)

    def decrypt(self, ciphertext: str | None, *, key_version: str, aad: str) -> str | None:
        if ciphertext is None:
            return None
        key = self._keys.get(key_version)
        if key is None:
            raise TokenEncryptionError("OAuth token key version is unavailable")
        try:
            payload = base64.urlsafe_b64decode(ciphertext)
            if len(payload) < 29:
                raise ValueError("ciphertext is too short")
            return AESGCM(key).decrypt(payload[:12], payload[12:], aad.encode("utf-8")).decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise TokenEncryptionError("OAuth token decryption failed") from exc

    def needs_rotation(self, key_version: str) -> bool:
        return key_version != self.active_version
