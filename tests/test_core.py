from __future__ import annotations

import base64
import secrets

import pytest
from cryptography.exceptions import InvalidTag

from app.crypto import SecretVault, mask_email
from app.services import AccountFormatError, parse_account_line, sanitize_error


def test_parse_original_four_part_format_and_token_delimiter():
    account = parse_account_line(
        "User@Example.invalid----secret----client-id----refresh-token-value-that-is-long----tail"
    )
    assert account is not None
    assert account.email == "user@example.invalid"
    assert account.password == "secret"  # pragma: allowlist secret
    assert account.refresh_token.endswith("----tail")


def test_parse_ignores_blank_and_comment_lines():
    assert parse_account_line("   ") is None
    assert parse_account_line("# 导入说明") is None


@pytest.mark.parametrize(
    "line",
    [
        "missing-delimiters",
        "bad-email----pw----client----a-refresh-token-that-is-long-enough",
        "ok@example.invalid----pw--------a-refresh-token-that-is-long-enough",
        "ok@example.invalid----pw----client----short",
    ],
)
def test_parse_rejects_invalid_rows(line):
    with pytest.raises(AccountFormatError):
        parse_account_line(line)


def test_vault_round_trip_and_ciphertext_does_not_contain_plaintext():
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    vault = SecretVault(key)
    secret = "a-sensitive-refresh-token-value"  # pragma: allowlist secret
    context = "account:" + "a" * 64 + ":refresh_token"
    ciphertext = vault.seal(secret, context)
    assert secret.encode() not in ciphertext
    assert vault.open(ciphertext, context) == secret
    with pytest.raises(InvalidTag):
        vault.open(ciphertext, "account:" + "b" * 64 + ":refresh_token")
    assert vault.lookup_hash("USER@EXAMPLE.INVALID") == vault.lookup_hash("user@example.invalid")


def test_privacy_helpers_mask_and_redact():
    assert mask_email("someone@example.invalid").startswith("so")
    error = sanitize_error("refresh_token=very-secret access_token=also-secret")
    assert "very-secret" not in error
    assert "also-secret" not in error
