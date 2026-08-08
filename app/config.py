from __future__ import annotations

import os
from base64 import b64decode
from binascii import Error as BinasciiError
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum}–{maximum} 之间")
    return value


def _csv(name: str, default: str) -> frozenset[str]:
    return frozenset(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development").strip().lower()
    app_name: str = os.getenv("APP_NAME", "令牌织机 · TokenLoom")
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'dev.db').as_posix()}"
    )
    admin_key_hash: str = os.getenv("ADMIN_KEY_HASH", "")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    encryption_key: str = os.getenv("ENCRYPTION_KEY", "")
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
    )
    cookie_secure: bool = _bool("COOKIE_SECURE", False)
    trust_proxy_headers: bool = _bool("TRUST_PROXY_HEADERS", False)
    trusted_proxy_ips: frozenset[str] = _csv("TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    allowed_hosts: frozenset[str] = _csv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver")
    session_hours: int = _int("SESSION_HOURS", 12, 1, 168)
    bind_host: str = os.getenv("BIND_HOST", "127.0.0.1")
    bind_port: int = _int("BIND_PORT", 8787, 1, 65535)
    max_upload_bytes: int = _int("MAX_UPLOAD_BYTES", 2 * 1024**3, 1024, 20 * 1024**3)
    min_free_bytes: int = _int("MIN_FREE_BYTES", 2 * 1024**3, 0, 100 * 1024**3)
    max_import_line_bytes: int = _int("MAX_IMPORT_LINE_BYTES", 256 * 1024, 1024, 4 * 1024**2)
    import_batch_size: int = _int("IMPORT_BATCH_SIZE", 1000, 1, 10_000)
    worker_batch_size: int = _int("WORKER_BATCH_SIZE", 500, 1, 5000)
    audit_retention_days: int = _int("AUDIT_RETENTION_DAYS", 180, 7, 3650)
    job_retention_days: int = _int("JOB_RETENTION_DAYS", 90, 7, 3650)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def production_ready(self) -> bool:
        try:
            encryption_key_valid = (
                len(b64decode(self.encryption_key.encode("ascii"), altchars=b"-_", validate=True)) == 32
            )
        except (BinasciiError, UnicodeEncodeError, ValueError):
            encryption_key_valid = False
        return bool(
            self.admin_key_hash.startswith("$argon2")
            and encryption_key_valid
            and len(self.session_secret) >= 32
        )

    def validate(self) -> None:
        if self.app_env not in {"development", "test", "production"}:
            raise ValueError("APP_ENV 只能是 development、test 或 production")
        if not self.production_ready:
            raise RuntimeError("拒绝使用缺失、弱或格式错误的管理员、会话或字段加密密钥")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise RuntimeError("ALLOWED_HOSTS 必须列出明确主机，不能留空或使用通配符")
        for value in self.trusted_proxy_ips:
            try:
                ip_address(value)
            except ValueError as exc:
                raise ValueError(f"TRUSTED_PROXY_IPS 包含无效 IP：{value}") from exc


settings = Settings()
settings.validate()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "imports").mkdir(parents=True, exist_ok=True)
