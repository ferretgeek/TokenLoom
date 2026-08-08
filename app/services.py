from __future__ import annotations

import asyncio
import imaplib
import json
import re
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .crypto import mask_email, normalize_email, vault
from .database import engine
from .models import Account, AppSetting, AuditEvent, Job, utcnow

TOKEN_URL = "https://login.live.com/oauth20_token.srf"
REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
USER_AGENT = "TokenLoom/1.0 (+https://github.com/ferretgeek/TokenLoom)"
MAX_OAUTH_RESPONSE_BYTES = 64 * 1024
PERMANENT_ERRORS = {
    "invalid_grant",
    "expired_token",
    "invalid_client",
    "unauthorized_client",
    "interaction_required",
    "consent_required",
}
STATUS_LABELS = {
    "unknown": "待检测",
    "healthy": "取件正常",
    "refreshed": "刷新成功",
    "token_invalid": "令牌失效",
    "fetch_failed": "取件异常",
    "transient_error": "暂时异常",
}
DEFAULT_APP_SETTINGS = {
    "refresh_policy": {
        "interval_days": 60,
        "schedule_time": "03:30",
        "timezone": "Asia/Shanghai",
        "refresh_concurrency": 40,
        "health_concurrency": 8,
        "max_retries": 3,
    },
    "last_scheduler_run": {"date": "", "at": ""},
}


class AccountFormatError(ValueError):
    pass


@dataclass(slots=True)
class ParsedAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str


@dataclass(slots=True)
class WorkAccount:
    id: int
    email: str
    client_id: str
    refresh_token: str


@dataclass(slots=True)
class TokenResult:
    ok: bool
    access_token: str = ""
    refresh_token: str = ""
    error_code: str = ""
    error_summary: str = ""
    permanent: bool = False


@dataclass(slots=True)
class AccountResult:
    account_id: int
    ok: bool
    refresh_token: str = ""
    error_code: str = ""
    error_summary: str = ""
    permanent: bool = False
    health_checked: bool = False


def parse_account_line(raw: str) -> ParsedAccount | None:
    line = raw.strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    parts = line.split("----", 3)
    if len(parts) != 4:
        raise AccountFormatError("应为 4 段：邮箱----密码----client_id----refresh_token")
    email, password, client_id, token = (item.strip() for item in parts)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise AccountFormatError("邮箱格式不正确")
    if not client_id:
        raise AccountFormatError("client_id 不能为空")
    if len(token) < 20:
        raise AccountFormatError("refresh_token 为空或过短")
    if any(ord(char) < 32 for char in email + client_id + token):
        raise AccountFormatError("字段包含不可见控制字符")
    return ParsedAccount(normalize_email(email), password, client_id, token)


def sanitize_error(value: object, limit: int = 420) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "未知错误"))
    text = re.sub(
        r'(?i)(["\']?(?:refresh_token|access_token|password)["\']?\s*[:=]\s*)["\']?[^\s,"\'}]+',
        r"\1[已隐藏]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]+", "Bearer [已隐藏]", text)
    text = re.sub(r"[^\s@]+@[^\s@]+\.[^\s@]+", _redact_email_match, text)
    return text[:limit]


def _redact_email_match(match: re.Match[str]) -> str:
    raw = match.group(0)
    email = raw.rstrip(".,;:)]}")
    return mask_email(email) + raw[len(email) :]


async def ensure_defaults(session: AsyncSession) -> None:
    insert_factory = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    for key, value in DEFAULT_APP_SETTINGS.items():
        statement = insert_factory(AppSetting).values(key=key, value=value)
        await session.execute(statement.on_conflict_do_nothing(index_elements=[AppSetting.key]))
    await session.commit()


async def get_policy(session: AsyncSession) -> dict:
    row = await session.get(AppSetting, "refresh_policy")
    merged = dict(DEFAULT_APP_SETTINGS["refresh_policy"])
    if row:
        merged.update(row.value or {})
    return merged


async def add_audit(session: AsyncSession, action: str, detail: dict, ip_hash: str = "") -> None:
    session.add(AuditEvent(action=action, detail=detail, ip_hash=ip_hash))


def account_row(parsed: ParsedAccount, source_name: str, interval_days: int) -> dict:
    now = utcnow()
    return {
        "email_encrypted": vault.seal(parsed.email),
        "email_hash": vault.lookup_hash(parsed.email),
        "email_masked": mask_email(parsed.email),
        "domain": parsed.email.rsplit("@", 1)[-1],
        # The legacy four-part format contains a password, but OAuth refresh and
        # IMAP XOAUTH2 do not need it. Discard it instead of retaining needless PII.
        "client_id_encrypted": vault.seal(parsed.client_id),
        "refresh_token_encrypted": vault.seal(parsed.refresh_token),
        "status": "unknown",
        "source_name": source_name[:255],
        "next_refresh_at": now + timedelta(days=interval_days),
        "last_error_code": None,
        "last_error_summary": None,
        "created_at": now,
        "updated_at": now,
    }


async def upsert_accounts(
    session: AsyncSession,
    parsed: Iterable[ParsedAccount],
    source_name: str,
    interval_days: int,
    duplicate_mode: str,
) -> tuple[int, int]:
    rows = [account_row(item, source_name, interval_days) for item in parsed]
    if not rows:
        return 0, 0
    insert_factory = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    statement = insert_factory(Account).values(rows)
    if duplicate_mode == "update":
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[Account.email_hash],
            set_={
                "email_encrypted": excluded.email_encrypted,
                "email_masked": excluded.email_masked,
                "domain": excluded.domain,
                "client_id_encrypted": excluded.client_id_encrypted,
                "refresh_token_encrypted": excluded.refresh_token_encrypted,
                "status": "unknown",
                "source_name": excluded.source_name,
                "next_refresh_at": excluded.next_refresh_at,
                "last_error_code": None,
                "last_error_summary": None,
                "updated_at": excluded.updated_at,
            },
        )
        result = await session.execute(statement)
        return len(rows), 0
    statement = statement.on_conflict_do_nothing(index_elements=[Account.email_hash])
    result = await session.execute(statement)
    inserted = max(int(result.rowcount or 0), 0)
    return inserted, len(rows) - inserted


async def refresh_token(
    session: aiohttp.ClientSession,
    client_id: str,
    old_token: str,
    max_retries: int,
) -> TokenResult:
    for attempt in range(max(1, max_retries)):
        try:
            async with session.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": old_token,
                    "redirect_uri": REDIRECT_URI,
                },
                headers={"User-Agent": USER_AGENT},
            ) as response:
                raw_bytes = await response.content.read(MAX_OAUTH_RESPONSE_BYTES + 1)
                raw = raw_bytes[:MAX_OAUTH_RESPONSE_BYTES].decode(
                    response.charset or "utf-8", errors="replace"
                )
                try:
                    data = json.loads(raw) if len(raw_bytes) <= MAX_OAUTH_RESPONSE_BYTES else {}
                except json.JSONDecodeError:
                    data = {}
                if response.status == 200 and data.get("access_token"):
                    return TokenResult(
                        ok=True,
                        access_token=str(data["access_token"]),
                        refresh_token=str(data.get("refresh_token") or old_token),
                    )
                code = str(data.get("error") or f"http_{response.status}")
                description = sanitize_error(data.get("error_description") or f"HTTP {response.status}")
                permanent = code in PERMANENT_ERRORS or any(item in description for item in PERMANENT_ERRORS)
                retryable = response.status == 429 or response.status >= 500 or not permanent
                if permanent or not retryable or attempt + 1 >= max_retries:
                    return TokenResult(False, error_code=code, error_summary=description, permanent=permanent)
                retry_after = response.headers.get("Retry-After", "")
                delay = min(float(retry_after), 30.0) if retry_after.isdigit() else min(2**attempt, 8)
                await asyncio.sleep(delay)
        except (TimeoutError, aiohttp.ClientError) as exc:
            if attempt + 1 >= max_retries:
                return TokenResult(False, error_code="network_error", error_summary=sanitize_error(exc))
            await asyncio.sleep(min(2**attempt, 8))
        except Exception as exc:
            return TokenResult(False, error_code="internal_error", error_summary=sanitize_error(exc))
    return TokenResult(False, error_code="unknown", error_summary="刷新未返回结果")


def _imap_health(email: str, access_token: str, timeout: int = 25) -> tuple[bool, str]:
    client: imaplib.IMAP4_SSL | None = None
    try:
        context = ssl.create_default_context()
        client = imaplib.IMAP4_SSL("outlook.office365.com", 993, ssl_context=context, timeout=timeout)
        payload = f"user={email}\x01auth=Bearer {access_token}\x01\x01".encode()
        client.authenticate("XOAUTH2", lambda _: payload)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            return False, "IMAP 无法只读打开收件箱"
        return True, ""
    except Exception as exc:
        return False, sanitize_error(exc)
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


async def run_account_batch(
    accounts: list[WorkAccount],
    kind: str,
    concurrency: int,
    max_retries: int,
) -> list[AccountResult]:
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 150)))
    timeout = aiohttp.ClientTimeout(total=45, connect=10, sock_read=35)
    connector = aiohttp.TCPConnector(
        limit=max(concurrency + 10, 30), limit_per_host=max(concurrency, 10), ttl_dns_cache=600
    )

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http:

        async def one(account: WorkAccount) -> AccountResult:
            async with semaphore:
                token = await refresh_token(http, account.client_id, account.refresh_token, max_retries)
                if not token.ok:
                    return AccountResult(
                        account.id,
                        False,
                        error_code=token.error_code,
                        error_summary=token.error_summary,
                        permanent=token.permanent,
                        health_checked=kind == "health",
                    )
                if kind == "health":
                    ok, error = await asyncio.to_thread(_imap_health, account.email, token.access_token)
                    if not ok:
                        return AccountResult(
                            account.id,
                            False,
                            refresh_token=token.refresh_token,
                            error_code="imap_failed",
                            error_summary=error,
                            health_checked=True,
                        )
                return AccountResult(
                    account.id,
                    True,
                    refresh_token=token.refresh_token,
                    health_checked=kind == "health",
                )

        return list(await asyncio.gather(*(one(account) for account in accounts)))


def work_account(account: Account) -> WorkAccount:
    return WorkAccount(
        id=account.id,
        email=vault.open(account.email_encrypted),
        client_id=vault.open(account.client_id_encrypted),
        refresh_token=vault.open(account.refresh_token_encrypted),
    )


def account_scope_clause(job: Job):
    clauses = [Account.id > job.cursor_id]
    max_account_id = int((job.options or {}).get("max_account_id") or 0)
    if max_account_id:
        clauses.append(Account.id <= max_account_id)
    if job.scope == "selected":
        ids = sorted({int(value) for value in (job.account_ids or [])})
        clauses.append(Account.id.in_(ids or [-1]))
    elif job.scope == "due":
        clauses.extend([Account.next_refresh_at.is_not(None), Account.next_refresh_at <= utcnow()])
    return and_(*clauses)


async def count_scope(session: AsyncSession, scope: str, account_ids: list[int] | None = None) -> int:
    total, _max_account_id = await scope_snapshot(session, scope, account_ids)
    return total


async def scope_snapshot(
    session: AsyncSession,
    scope: str,
    account_ids: list[int] | None = None,
) -> tuple[int, int]:
    filters = []
    if scope == "selected":
        filters.append(Account.id.in_(account_ids or [-1]))
    elif scope == "due":
        filters.extend([Account.next_refresh_at.is_not(None), Account.next_refresh_at <= utcnow()])
    row = (await session.execute(select(func.count(Account.id), func.max(Account.id)).where(*filters))).one()
    return int(row[0] or 0), int(row[1] or 0)


async def dashboard_counts(session: AsyncSession) -> dict:
    now = utcnow()
    soon = now + timedelta(days=14)
    counts = (
        await session.execute(
            select(
                func.count(Account.id),
                func.count(Account.id).filter(Account.status.in_(["healthy", "refreshed"])),
                func.count(Account.id).filter(Account.status == "token_invalid"),
                func.count(Account.id).filter(Account.status.in_(["fetch_failed", "transient_error"])),
                func.count(Account.id).filter(Account.status == "unknown"),
                func.count(Account.id).filter(
                    Account.token_expires_at.is_not(None),
                    Account.token_expires_at > now,
                    Account.token_expires_at <= soon,
                ),
                func.count(Account.id).filter(
                    Account.next_refresh_at.is_not(None), Account.next_refresh_at <= now
                ),
            )
        )
    ).one()
    active_jobs = int(
        (await session.scalar(select(func.count(Job.id)).where(Job.status.in_(["queued", "running"])))) or 0
    )
    return {
        "total": int(counts[0] or 0),
        "healthy": int(counts[1] or 0),
        "invalid": int(counts[2] or 0),
        "attention": int(counts[3] or 0),
        "unknown": int(counts[4] or 0),
        "expiring": int(counts[5] or 0),
        "overdue": int(counts[6] or 0),
        "active_jobs": active_jobs,
    }


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def account_public(account: Account) -> dict:
    return {
        "id": account.id,
        "email": account.email_masked,
        "domain": account.domain,
        "status": account.status,
        "status_label": STATUS_LABELS.get(account.status, account.status),
        "source": account.source_name,
        "last_refresh_at": iso(account.last_refresh_at),
        "last_health_at": iso(account.last_health_at),
        "token_expires_at": iso(account.token_expires_at),
        "next_refresh_at": iso(account.next_refresh_at),
        "error": account.last_error_summary,
        "created_at": iso(account.created_at),
    }


def job_public(job: Job) -> dict:
    progress = (
        round(job.processed / job.total * 100, 1) if job.total else (100 if job.status == "completed" else 0)
    )
    return {
        "id": job.id,
        "kind": job.kind,
        "scope": job.scope,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "succeeded": job.succeeded,
        "failed": job.failed,
        "skipped": job.skipped,
        "progress": max(0, min(100, progress)),
        "source_name": job.source_name,
        "error": job.error_summary,
        "cancel_requested": job.cancel_requested,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
    }
