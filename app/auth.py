from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from ipaddress import ip_address

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status

from .config import settings

COOKIE_NAME = "token_loom_session"
HTTP_COOKIE_NAME = f"{COOKIE_NAME}_http"
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_attempts: dict[str, deque[float]] = defaultdict(deque)
_ATTEMPT_WINDOW_SECONDS = 15 * 60
_MAX_FAILED_ATTEMPTS = 8
_MAX_TRACKED_CLIENTS = 4096


def verify_admin_key(value: str) -> bool:
    if not settings.admin_key_hash:
        return False
    try:
        return _hasher.verify(settings.admin_key_hash, value)
    except (VerifyMismatchError, InvalidHashError):
        return False


def login_allowed(ip: str) -> bool:
    now = time.time()
    window = _attempts[ip]
    while window and window[0] < now - _ATTEMPT_WINDOW_SECONDS:
        window.popleft()
    if not window:
        _attempts.pop(ip, None)
        return True
    return len(window) < _MAX_FAILED_ATTEMPTS


def record_failed_login(ip: str) -> None:
    if ip not in _attempts and len(_attempts) >= _MAX_TRACKED_CLIENTS:
        now = time.time()
        expired = [
            key
            for key, values in _attempts.items()
            if not values or values[-1] < now - _ATTEMPT_WINDOW_SECONDS
        ]
        for key in expired:
            _attempts.pop(key, None)
        while len(_attempts) >= _MAX_TRACKED_CLIENTS:
            _attempts.pop(next(iter(_attempts)))
    _attempts[ip].append(time.time())


def clear_failed_logins(ip: str) -> None:
    _attempts.pop(ip, None)


def _sign(payload: bytes) -> str:
    return hmac.new(settings.session_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def create_session() -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    payload = json.dumps(
        {"iat": int(time.time()), "exp": int(time.time()) + settings.session_hours * 3600, "csrf": csrf},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{encoded}.{_sign(encoded.encode('ascii'))}", csrf


def request_is_https(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    forwarded = ""
    if settings.trust_proxy_headers and peer in settings.trusted_proxy_ips:
        forwarded = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def session_cookie_name(request: Request) -> str:
    # HTTP 与 HTTPS 使用不同 Cookie 名, 避免浏览器中的 Secure Cookie 阻止
    # HTTP 登录覆盖同名会话, 导致验证成功后又被送回登录页。
    return COOKIE_NAME if request_is_https(request) else HTTP_COOKIE_NAME


def read_session(request: Request) -> dict | None:
    cookie_name = session_cookie_name(request)
    token = request.cookies.get(cookie_name, "")
    if not token and cookie_name == HTTP_COOKIE_NAME:
        # 兼容升级前通过 HTTP 建立的会话; 新会话一律使用独立名称。
        token = request.cookies.get(COOKIE_NAME, "")
    try:
        encoded, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(_sign(encoded.encode("ascii")), signature):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        now = int(time.time())
        issued_at = int(payload.get("iat", 0))
        expires_at = int(payload.get("exp", 0))
        csrf = payload.get("csrf")
        if (
            issued_at > now + 60
            or expires_at < now
            or expires_at - issued_at > settings.session_hours * 3600 + 60
        ):
            return None
        if not isinstance(csrf, str) or not 16 <= len(csrf) <= 128:
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def require_session(request: Request) -> dict:
    session = read_session(request)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return session


def require_csrf(request: Request) -> dict:
    session = require_session(request)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not hmac.compare_digest(str(session.get("csrf", "")), supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="安全校验失败，请刷新页面")
    return session


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if settings.trust_proxy_headers and peer in settings.trusted_proxy_ips:
        candidate = request.headers.get("X-Real-IP", "").strip()
        if candidate:
            try:
                return str(ip_address(candidate))
            except ValueError:
                pass
    try:
        return str(ip_address(peer))
    except ValueError:
        return "unknown"


def request_uses_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    return settings.trust_proxy_headers and peer in settings.trusted_proxy_ips


def hash_ip(ip: str) -> str:
    return hmac.new(settings.session_secret.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()
