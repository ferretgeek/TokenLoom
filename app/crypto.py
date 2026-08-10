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

    _BOUND_PREFIX = b"TL2\x00"

    def __init__(self, encoded_key: str):
        key = base64.b64decode(encoded_key.encode("ascii"), altchars=b"-_", validate=True)
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY 必须是 32 字节的 URL-safe Base64")
        self._key = key
        self._aes = AESGCM(key)

    @staticmethod
    def _aad(context: str) -> bytes:
        if not context or len(context.encode("utf-8")) > 256:
            raise ValueError("加密上下文无效")
        return b"token-loom:v2:" + context.encode("utf-8")

    def seal(self, value: str, context: str) -> bytes:
        raw = zlib.compress(value.encode("utf-8"), level=6)
        nonce = secrets.token_bytes(12)
        return self._BOUND_PREFIX + nonce + self._aes.encrypt(nonce, raw, self._aad(context))

    def open(self, value: bytes | None, context: str) -> str:
        if not value:
            return ""
        if value.startswith(self._BOUND_PREFIX):
            nonce, payload = value[4:16], value[16:]
            raw = self._aes.decrypt(nonce, payload, self._aad(context))
        else:
            nonce, payload = value[:12], value[12:]
            raw = self._aes.decrypt(nonce, payload, b"token-admin:v1")
        return zlib.decompress(raw).decode("utf-8")

    def needs_upgrade(self, value: bytes | None) -> bool:
        return bool(value) and not value.startswith(self._BOUND_PREFIX)

    def upgrade(self, value: bytes, context: str) -> bytes:
        if not self.needs_upgrade(value):
            return value
        return self.seal(self.open(value, context), context)

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


def account_cipher_context(email_hash: str, field: str) -> str:
    valid_hash = len(email_hash) == 64 and all(character in "0123456789abcdef" for character in email_hash)
    if field not in {"email", "client_id", "refresh_token"} or not valid_hash:
        raise ValueError("账号加密上下文无效")
    return f"account:{email_hash}:{field}"
