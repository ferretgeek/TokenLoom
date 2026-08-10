from __future__ import annotations

import asyncio
import logging
import re
import signal
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select, update

from .config import settings
from .crypto import account_cipher_context, vault
from .database import SessionLocal, init_db
from .models import Account, AppSetting, AuditEvent, Job, utcnow
from .services import (
    AccountFormatError,
    account_scope_clause,
    ensure_defaults,
    get_policy,
    parse_account_line,
    run_account_batch,
    sanitize_error,
    scope_snapshot,
    upsert_accounts,
    work_account,
)

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("token-loom.worker")
stop_event = asyncio.Event()
IMPORTS_DIR = (settings.data_dir / "imports").resolve()
SPOOL_NAME = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.txt",
    re.IGNORECASE,
)


def safe_spool_path(value: str | Path) -> Path:
    path = Path(value).resolve(strict=False)
    if path.parent != IMPORTS_DIR or not SPOOL_NAME.fullmatch(path.name):
        raise RuntimeError("导入临时文件路径无效")
    return path


def remove_import_spool(value: str | Path | None) -> None:
    if not value:
        return
    try:
        path = safe_spool_path(value)
    except RuntimeError as exc:
        log.warning("refused unsafe import spool path: %s", sanitize_error(exc))
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("cannot remove import spool %s: %s", path.name, sanitize_error(exc))


def read_limited_line(handle: BinaryIO, limit: int) -> tuple[bytes, bool]:
    raw = handle.readline(limit + 1)
    if not raw:
        return b"", False
    too_long = len(raw) > limit
    while raw and not raw.endswith(b"\n"):
        chunk = handle.readline(limit + 1)
        if not chunk:
            break
        too_long = too_long or len(chunk) > limit
        raw = chunk if too_long else raw + chunk
    return (b"" if too_long else raw), too_long


async def recover_stale_jobs() -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(Job)
            .where(Job.status == "running")
            .values(status="queued", heartbeat_at=utcnow(), error_summary="Worker 重启后自动续跑")
        )
        await session.commit()


async def maintenance_tick() -> None:
    now = utcnow()
    async with SessionLocal() as session:
        await session.execute(
            delete(AuditEvent).where(
                AuditEvent.created_at < now - timedelta(days=settings.audit_retention_days)
            )
        )
        await session.execute(
            delete(Job).where(
                Job.status.in_(["completed", "failed", "cancelled"]),
                Job.finished_at.is_not(None),
                Job.finished_at < now - timedelta(days=settings.job_retention_days),
            )
        )
        active_paths = set(
            (
                await session.scalars(
                    select(Job.source_path).where(
                        Job.kind == "import",
                        Job.status.in_(["queued", "running"]),
                        Job.source_path.is_not(None),
                    )
                )
            ).all()
        )
        await session.commit()

    cutoff = time.time() - 3600
    for path in IMPORTS_DIR.iterdir():
        try:
            if not path.is_file() or not SPOOL_NAME.fullmatch(path.name):
                continue
            if str(path) not in active_paths and path.stat().st_mtime < cutoff:
                remove_import_spool(path)
        except OSError:
            continue


async def scheduler_tick() -> None:
    async with SessionLocal() as session:
        policy_row = await session.get(AppSetting, "refresh_policy")
        policy = policy_row.value if policy_row else {}
        timezone_name = str(policy.get("timezone", "Asia/Shanghai"))
        try:
            zone = ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError):
            zone = ZoneInfo("Asia/Shanghai")
        local_now = datetime.now(zone)
        scheduled_time = str(policy.get("schedule_time", "03:30"))
        try:
            hour, minute = (int(item) for item in scheduled_time.split(":", 1))
        except (TypeError, ValueError):
            hour, minute = 3, 30
        if (local_now.hour, local_now.minute) < (hour, minute):
            return
        marker = await session.get(AppSetting, "last_scheduler_run", with_for_update=True)
        today = local_now.date().isoformat()
        if marker and marker.value.get("date") == today:
            return
        existing = await session.scalar(
            select(Job.id).where(
                Job.kind == "refresh", Job.scope == "due", Job.status.in_(["queued", "running"])
            )
        )
        if not existing:
            total, max_account_id = await scope_snapshot(session, "due")
            if total:
                session.add(
                    Job(
                        kind="refresh",
                        scope="due",
                        status="queued",
                        priority=20,
                        total=total,
                        options={"max_account_id": max_account_id},
                    )
                )
        marker.value = {"date": today, "at": local_now.isoformat()}
        await session.commit()


async def claim_job() -> str | None:
    async with SessionLocal() as session:
        statement = (
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.priority.desc(), Job.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = await session.scalar(statement)
        if not job:
            return None
        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = utcnow()
            source_path = job.source_path if job.kind == "import" else None
            await session.commit()
            remove_import_spool(source_path)
            return None
        job.status = "running"
        job.started_at = job.started_at or utcnow()
        job.heartbeat_at = utcnow()
        await session.commit()
        return job.id


async def process_import(job_id: str) -> None:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job or not job.source_path:
            raise RuntimeError("导入文件不存在")
        path = safe_spool_path(job.source_path)
        offset = int(job.cursor_id)
        duplicate_mode = str((job.options or {}).get("duplicate_mode", "update"))
        policy = await get_policy(session)
        interval_days = int(policy.get("interval_days", 60))
        source_name = job.source_name or "TXT 导入"

    parsed = []
    failed = 0
    processed = 0
    errors: list[str] = []
    eof = False
    new_offset = offset
    with path.open("rb") as handle:
        handle.seek(offset)
        for _ in range(settings.import_batch_size):
            raw, too_long = read_limited_line(handle, settings.max_import_line_bytes)
            new_offset = handle.tell()
            if too_long:
                processed += 1
                failed += 1
                if len(errors) < 5:
                    errors.append(f"第 {job.processed + processed} 行：单行超过安全长度限制")
                continue
            if not raw:
                eof = True
                break
            processed += 1
            try:
                text = raw.decode("utf-8-sig" if offset == 0 and processed == 1 else "utf-8")
                item = parse_account_line(text)
                if item:
                    parsed.append(item)
            except (UnicodeDecodeError, AccountFormatError) as exc:
                failed += 1
                if len(errors) < 5:
                    errors.append(f"第 {job.processed + processed} 行：{sanitize_error(exc, 120)}")

    async with SessionLocal() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        inserted, skipped = await upsert_accounts(session, parsed, source_name, interval_days, duplicate_mode)
        job.cursor_id = new_offset
        job.processed += processed
        job.succeeded += inserted
        job.failed += failed
        job.skipped += skipped + max(0, processed - len(parsed) - failed)
        job.error_summary = "；".join(errors) or job.error_summary
        job.heartbeat_at = utcnow()
        if eof or job.cancel_requested:
            job.status = "cancelled" if job.cancel_requested else "completed"
            job.finished_at = utcnow()
        else:
            job.status = "queued"
        await session.commit()

    if eof or job.cancel_requested:
        remove_import_spool(path)


async def load_work(job_id: str) -> tuple[Job, list]:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            raise RuntimeError("任务不存在")
        statement = (
            select(Account)
            .where(account_scope_clause(job))
            .order_by(Account.id.asc())
            .limit(settings.worker_batch_size)
        )
        rows = list((await session.scalars(statement)).all())
        return job, [work_account(row) for row in rows]


async def process_account_job(job_id: str) -> None:
    job, accounts = await load_work(job_id)
    if not accounts:
        async with SessionLocal() as session:
            current = await session.get(Job, job_id, with_for_update=True)
            current.status = "cancelled" if current.cancel_requested else "completed"
            current.finished_at = utcnow()
            current.heartbeat_at = utcnow()
            await session.commit()
        return

    async with SessionLocal() as session:
        policy = await get_policy(session)
    concurrency = int(policy.get("health_concurrency" if job.kind == "health" else "refresh_concurrency", 8))
    max_retries = int(policy.get("max_retries", 3))
    interval_days = int(policy.get("interval_days", 60))
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_job(job_id, heartbeat_stop))
    try:
        results = await run_account_batch(accounts, job.kind, concurrency, max_retries)
    finally:
        heartbeat_stop.set()
        await heartbeat_task
    result_map = {item.account_id: item for item in results}
    now = utcnow()

    async with SessionLocal() as session:
        current = await session.get(Job, job_id, with_for_update=True)
        db_accounts = list(
            (await session.scalars(select(Account).where(Account.id.in_(list(result_map))))).all()
        )
        success = 0
        failed = 0
        for account in db_accounts:
            result = result_map[account.id]
            account.last_refresh_at = now
            if result.refresh_token:
                account.refresh_token_encrypted = vault.seal(
                    result.refresh_token,
                    account_cipher_context(account.email_hash, "refresh_token"),
                )
                account.last_refresh_success_at = now
                account.token_expires_at = now + timedelta(days=90)
                account.next_refresh_at = now + timedelta(days=interval_days)
            if result.health_checked:
                account.last_health_at = now
            if result.ok:
                success += 1
                account.status = "healthy" if result.health_checked else "refreshed"
                if result.health_checked:
                    account.last_health_success_at = now
                account.last_error_code = None
                account.last_error_summary = None
            else:
                failed += 1
                if result.permanent:
                    account.status = "token_invalid"
                    account.next_refresh_at = None
                elif result.error_code == "imap_failed":
                    account.status = "fetch_failed"
                else:
                    account.status = "transient_error"
                    account.next_refresh_at = now + timedelta(days=1)
                account.last_error_code = result.error_code[:80]
                account.last_error_summary = sanitize_error(result.error_summary)
            account.updated_at = now

        current.cursor_id = max(item.id for item in accounts)
        current.processed += len(accounts)
        current.succeeded += success
        current.failed += failed
        current.heartbeat_at = now
        if current.cancel_requested:
            current.status = "cancelled"
            current.finished_at = now
        elif current.processed >= current.total or len(accounts) < settings.worker_batch_size:
            current.status = "completed"
            current.finished_at = now
        else:
            current.status = "queued"
        await session.commit()


async def fail_job(job_id: str, exc: Exception) -> None:
    log.exception("job %s failed", job_id)
    async with SessionLocal() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        if job:
            source_path = job.source_path if job.kind == "import" else None
            job.status = "failed"
            job.error_summary = sanitize_error(exc, 900)
            job.finished_at = utcnow()
            await session.commit()
            remove_import_spool(source_path)


async def heartbeat_job(job_id: str, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=60)
        except TimeoutError:
            try:
                async with SessionLocal() as session:
                    await session.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.status == "running")
                        .values(heartbeat_at=utcnow())
                    )
                    await session.commit()
            except Exception as exc:
                log.warning("job heartbeat update failed: %s", sanitize_error(exc))


async def run() -> None:
    await init_db()
    async with SessionLocal() as session:
        await ensure_defaults(session)
    await recover_stale_jobs()
    await maintenance_tick()
    log.info("worker ready")
    loop = asyncio.get_running_loop()
    last_scheduler = 0.0
    last_maintenance = loop.time()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    while not stop_event.is_set():
        now = loop.time()
        if now - last_scheduler > 30:
            try:
                await scheduler_tick()
            except Exception:
                log.exception("scheduler tick failed")
            last_scheduler = now
        if now - last_maintenance > 3600:
            try:
                await maintenance_tick()
            except Exception:
                log.exception("maintenance tick failed")
            last_maintenance = now
        job_id = await claim_job()
        if not job_id:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
            except TimeoutError:
                pass
            continue
        try:
            async with SessionLocal() as session:
                job = await session.get(Job, job_id)
                kind = job.kind if job else ""
            if kind == "import":
                await process_import(job_id)
            elif kind in {"refresh", "health"}:
                await process_account_job(job_id)
            else:
                raise RuntimeError(f"未知任务类型：{kind}")
        except Exception as exc:
            await fail_job(job_id, exc)
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(run())
