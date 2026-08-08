from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings


class SecretVault:
    """Small, authenticated encryption wrapper for account fields."""

    def __init__(self, encoded_key: str):
        key = base64.b64decode(encoded_key.encode("ascii"), altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY 必须是 32 字节的 URL-safe Base64")
        self._key = key
        self._aes = AESGCM(key)

    def seal(self, value: str) -> bytes:
        raw = zlib.compress(value.encode("utf-8"), level=6)
        nonce = secrets.token_bytes(12)
        return nonce + self._aes.encrypt(nonce, raw, b"token-admin:v1")

    def open(self, value: bytes | None) -> str:
        if not value:
            return ""
        nonce, payload = value[:12], value[12:]
        raw = self._aes.decrypt(nonce, payload, b"token-admin:v1")
        return zlib.decompress(raw).decode("utf-8")

    def lookup_hash(self, email: str) -> str:
        normalized = normalize_email(email)
        return hmac.new(self._key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def mask_email(value: str) -> str:
    value = normalize_email(value)
    if "@" not in value:
        return "***"
    local, domain = value.rsplit("@", 1)
    if len(local) <= 2:
        visible = local[:1]
    else:
        visible = local[:2]
    return f"{visible}{'•' * min(max(len(local) - len(visible), 2), 8)}@{domain}"


vault = SecretVault(settings.encryption_key)
