from __future__ import annotations

import base64
import io
import secrets
import uuid

import pytest
from argon2 import PasswordHasher
from starlette.requests import Request

from app.auth import COOKIE_NAME, HTTP_COOKIE_NAME, create_session, read_session, session_cookie_name
from app.config import Settings
from app.models import Job
from app.services import ParsedAccount, account_row, job_public, sanitize_error
from app.worker import read_limited_line, remove_import_spool, safe_spool_path


def request_with_cookie(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"{COOKIE_NAME}={token}".encode("ascii"))],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
        }
    )


def test_session_rejects_tampering():
    token, csrf = create_session()
    assert read_session(request_with_cookie(token))["csrf"] == csrf
    replacement = "0" if token[-1] != "0" else "1"
    assert read_session(request_with_cookie(token[:-1] + replacement)) is None


def test_http_and_https_use_separate_cookie_names():
    http_request = request_with_cookie("unused")
    https_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 443),
            "scheme": "https",
        }
    )
    assert session_cookie_name(http_request) == HTTP_COOKIE_NAME
    assert session_cookie_name(https_request) == COOKIE_NAME


def test_production_settings_reject_weak_secrets():
    with pytest.raises(RuntimeError, match="拒绝使用缺失"):
        Settings(app_env="production", admin_key_hash="", session_secret="", encryption_key="").validate()


def test_production_settings_accept_strong_secrets():
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    configured = Settings(
        app_env="production",
        admin_key_hash=PasswordHasher().hash("a-long-admin-key"),
        session_secret=secrets.token_urlsafe(48),
        encryption_key=key,
    )
    configured.validate()
    assert configured.production_ready


def test_all_modes_reject_weak_secrets_and_wildcard_hosts():
    with pytest.raises(RuntimeError, match="拒绝使用缺失"):
        Settings(app_env="development", admin_key_hash="", session_secret="", encryption_key="").validate()
    with pytest.raises(RuntimeError, match="通配符"):
        Settings(
            app_env="test",
            admin_key_hash=PasswordHasher().hash("a-long-admin-key"),
            session_secret=secrets.token_urlsafe(48),
            encryption_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
            allowed_hosts=frozenset({"*"}),
        ).validate()


def test_legacy_password_is_not_retained():
    parsed = ParsedAccount(
        email="person@example.invalid",
        password="must-not-be-stored",  # pragma: allowlist secret
        client_id="client-id",
        refresh_token="a-refresh-token-that-is-long-enough",
    )
    row = account_row(parsed, "unit-test", 60)
    assert "password_encrypted" not in row
    assert all(b"must-not-be-stored" not in value for value in row.values() if isinstance(value, bytes))


def test_error_sanitizer_redacts_tokens_bearer_and_email():
    error = sanitize_error(  # pragma: allowlist secret
        'password:"plain-secret" refresh_token=refresh-secret Bearer eyJ.secret.signature person@example.invalid'  # noqa: E501  # pragma: allowlist secret
    )
    for secret in ("plain-secret", "refresh-secret", "eyJ.secret.signature", "person@example.invalid"):
        assert secret not in error
    assert "[已隐藏]" in error
    assert "@example.invalid" in error


def test_limited_line_reader_drains_oversized_logical_line():
    handle = io.BytesIO(b"a" * 40 + b"\nnext\n")
    raw, too_long = read_limited_line(handle, 16)
    assert raw == b""
    assert too_long is True
    raw, too_long = read_limited_line(handle, 16)
    assert raw == b"next\n"
    assert too_long is False


def test_spool_cleanup_is_confined_to_import_directory(tmp_path, monkeypatch):
    from app import worker

    monkeypatch.setattr(worker, "IMPORTS_DIR", tmp_path.resolve())
    spool = tmp_path / f"{uuid.uuid4()}.txt"
    spool.write_text("temporary", encoding="utf-8")
    assert safe_spool_path(spool) == spool.resolve()
    remove_import_spool(spool)
    assert not spool.exists()

    outside = tmp_path.parent / f"{uuid.uuid4()}.txt"
    with pytest.raises(RuntimeError, match="路径无效"):
        safe_spool_path(outside)


def test_job_progress_is_clamped():
    job = Job(kind="refresh", scope="all", total=10, processed=15, status="completed")
    assert job_public(job)["progress"] == 100
