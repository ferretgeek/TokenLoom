from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import delete, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import (
    COOKIE_NAME,
    clear_failed_logins,
    client_ip,
    create_session,
    hash_ip,
    login_allowed,
    read_session,
    record_failed_login,
    request_is_https,
    require_csrf,
    require_session,
    session_cookie_name,
    verify_admin_key,
)
from .config import BASE_DIR, settings
from .crypto import vault
from .database import SessionLocal, engine, init_db
from .models import Account, AppSetting, Job
from .services import (
    STATUS_LABELS,
    account_public,
    add_audit,
    dashboard_counts,
    ensure_defaults,
    get_policy,
    job_public,
    scope_snapshot,
)

templates = Environment(
    loader=FileSystemLoader(BASE_DIR / "app" / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await ensure_defaults(session)
    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api/") or request.url.path in {"/", "/login"}:
        response.headers["Cache-Control"] = "no-store"
    if settings.app_env == "production" and request_is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def render(name: str, **context) -> HTMLResponse:
    return HTMLResponse(templates.get_template(name).render(**context))


def import_disk_budget() -> int:
    free = shutil.disk_usage(settings.data_dir).free
    return max(0, free - settings.min_free_bytes)


def new_spool(job_id: str):
    spool = settings.data_dir / "imports" / f"{job_id}.txt"
    descriptor = os.open(spool, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return spool, os.fdopen(descriptor, "wb")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "token-loom", "version": "1.0.0"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if read_session(request):
        return RedirectResponse("/", status_code=303)
    return render(
        "login.html",
        app_name=settings.app_name,
        error="",
        transport_secure=request_is_https(request),
    )


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, admin_key: str = Form(...)):
    admin_key = admin_key.strip()
    ip = client_ip(request)
    if not login_allowed(ip):
        return HTMLResponse(
            templates.get_template("login.html").render(
                app_name=settings.app_name,
                error="尝试次数过多，请 15 分钟后再试",
                transport_secure=request_is_https(request),
            ),
            status_code=429,
        )
    if len(admin_key.encode("utf-8")) > 512 or not await asyncio.to_thread(verify_admin_key, admin_key):
        record_failed_login(ip)
        return HTMLResponse(
            templates.get_template("login.html").render(
                app_name=settings.app_name,
                error="管理员密钥不正确",
                transport_secure=request_is_https(request),
            ),
            status_code=401,
        )
    clear_failed_logins(ip)
    token, _csrf = create_session()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        session_cookie_name(request),
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure or request_is_https(request),
        samesite="strict",
        path="/",
    )
    async with SessionLocal() as session:
        await add_audit(session, "login", {}, hash_ip(ip))
        await session.commit()
    return response


@app.post("/logout")
async def logout(request: Request):
    require_csrf(request)
    response = JSONResponse({"ok": True})
    cookie_name = session_cookie_name(request)
    response.delete_cookie(cookie_name, path="/", secure=request_is_https(request), samesite="strict")
    if cookie_name != COOKIE_NAME:
        response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    session = read_session(request)
    if not session:
        return RedirectResponse("/login", status_code=303)
    return render(
        "index.html",
        app_name=settings.app_name,
        csrf=session["csrf"],
        status_labels=STATUS_LABELS,
    )


@app.get("/api/dashboard")
async def dashboard(_session: dict = Depends(require_session)):
    async with SessionLocal() as db:
        counts = await dashboard_counts(db)
        policy = await get_policy(db)
        latest = list((await db.scalars(select(Job).order_by(Job.created_at.desc()).limit(6))).all())
    return {"counts": counts, "policy": policy, "jobs": [job_public(job) for job in latest]}


@app.get("/api/accounts")
async def list_accounts(
    _session: dict = Depends(require_session),
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=10, le=100),
    status: str = Query("all"),
    email: str = Query("", max_length=320),
    domain: str = Query("", max_length=255),
):
    async with SessionLocal() as db:
        statement = select(Account).where(Account.id > cursor)
        if status != "all":
            if status not in STATUS_LABELS:
                raise HTTPException(400, "未知状态筛选")
            statement = statement.where(Account.status == status)
        if email.strip():
            statement = statement.where(Account.email_hash == vault.lookup_hash(email))
        if domain.strip():
            statement = statement.where(Account.domain == domain.strip().lower())
        rows = list((await db.scalars(statement.order_by(Account.id.asc()).limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [account_public(row) for row in rows],
        "next_cursor": rows[-1].id if has_more and rows else None,
        "has_more": has_more,
    }


def safe_source_name(value: str) -> str:
    value = Path(value or "导入数据.txt").name
    value = re.sub(r"[^\w.\-()（）\u4e00-\u9fff ]+", "_", value, flags=re.UNICODE)
    return value[:255] or "导入数据.txt"


def payload_integer(payload: dict, key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise HTTPException(400, f"{key} 必须是整数")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} 必须是整数") from None


@app.post("/api/import/paste")
async def import_pasted_accounts(request: Request, payload: dict, _session: dict = Depends(require_csrf)):
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(400, "账号内容必须是文本")
    duplicate_mode = str(payload.get("duplicate_mode", "update"))
    if duplicate_mode not in {"update", "skip"}:
        raise HTTPException(400, "重复处理方式无效")
    if not text.strip():
        raise HTTPException(400, "请填写或粘贴账号")
    encoded = text.encode("utf-8")
    if len(encoded) > min(10 * 1024 * 1024, settings.max_upload_bytes):
        raise HTTPException(413, "粘贴内容上限为 10 MB，大文件请使用 TXT 导入")
    disk_budget = import_disk_budget()
    if disk_budget <= 0:
        raise HTTPException(507, "服务器已触及磁盘安全余量，已暂停导入")
    if len(encoded) > disk_budget:
        raise HTTPException(507, "导入会占用磁盘安全余量，请释放空间后重试")
    job_id = str(uuid.uuid4())
    spool: Path | None = None
    try:
        spool, handle = new_spool(job_id)
        with handle:
            handle.write(encoded)
        total_lines = encoded.count(b"\n") + (1 if not encoded.endswith(b"\n") else 0)
        display_name = safe_source_name(str(payload.get("source_name", "")) or "手动粘贴.txt")
        async with SessionLocal() as db:
            job = Job(
                id=job_id,
                kind="import",
                scope="file",
                status="queued",
                priority=70,
                total=total_lines,
                source_path=str(spool),
                source_name=display_name,
                options={"duplicate_mode": duplicate_mode},
            )
            db.add(job)
            await add_audit(
                db, "import_queued", {"job_id": job_id, "lines": total_lines}, hash_ip(client_ip(request))
            )
            await db.commit()
        return {"ok": True, "job": job_public(job)}
    except Exception:
        if spool is not None:
            spool.unlink(missing_ok=True)
        raise


@app.post("/api/import")
async def import_accounts(
    request: Request,
    _session: dict = Depends(require_csrf),
    text: str = Form(""),
    duplicate_mode: str = Form("update"),
    source_name: str = Form(""),
    file: UploadFile | None = File(None),
):
    if duplicate_mode not in {"update", "skip"}:
        raise HTTPException(400, "重复处理方式无效")
    if not text.strip() and not file:
        raise HTTPException(400, "请粘贴账号或选择 TXT 文件")
    if text and len(text.encode("utf-8")) > 10 * 1024 * 1024:
        raise HTTPException(413, "粘贴内容上限为 10 MB，大文件请使用 TXT 导入")
    disk_budget = import_disk_budget()
    if disk_budget <= 0:
        raise HTTPException(507, "服务器已触及磁盘安全余量，已暂停导入")

    job_id = str(uuid.uuid4())
    spool: Path | None = None
    total_bytes = 0
    total_lines = 0
    file_had_data = False
    file_ended_with_newline = True
    try:
        spool, handle = new_spool(job_id)
        with handle:
            if text.strip():
                payload = text.encode("utf-8")
                handle.write(payload)
                total_bytes += len(payload)
                total_lines += payload.count(b"\n") + (1 if payload and not payload.endswith(b"\n") else 0)
                if file and payload and not payload.endswith(b"\n"):
                    handle.write(b"\n")
                    total_bytes += 1
            if file:
                while chunk := await file.read(1024 * 1024):
                    file_had_data = True
                    file_ended_with_newline = chunk.endswith(b"\n")
                    total_bytes += len(chunk)
                    if total_bytes > settings.max_upload_bytes:
                        raise HTTPException(413, "TXT 文件超过服务器配置的导入上限")
                    if total_bytes > disk_budget:
                        raise HTTPException(507, "导入会占用磁盘安全余量，请释放空间后重试")
                    handle.write(chunk)
                    total_lines += chunk.count(b"\n")
                if file_had_data and not file_ended_with_newline:
                    total_lines += 1
        if total_bytes == 0:
            raise HTTPException(400, "导入内容为空")
        display_name = safe_source_name(source_name or (file.filename if file else "手动粘贴.txt"))
        async with SessionLocal() as db:
            job = Job(
                id=job_id,
                kind="import",
                scope="file",
                status="queued",
                priority=70,
                total=total_lines,
                source_path=str(spool),
                source_name=display_name,
                options={"duplicate_mode": duplicate_mode},
            )
            db.add(job)
            await add_audit(
                db, "import_queued", {"job_id": job_id, "lines": total_lines}, hash_ip(client_ip(request))
            )
            await db.commit()
        return {"ok": True, "job": job_public(job)}
    except Exception:
        if spool is not None:
            spool.unlink(missing_ok=True)
        raise
    finally:
        if file:
            await file.close()


@app.post("/api/jobs")
async def create_job(request: Request, payload: dict, _session: dict = Depends(require_csrf)):
    kind = str(payload.get("kind", ""))
    scope = str(payload.get("scope", "selected"))
    ids = payload.get("ids") or []
    if kind not in {"refresh", "health"}:
        raise HTTPException(400, "任务类型无效")
    if scope not in {"selected", "all", "due"}:
        raise HTTPException(400, "任务范围无效")
    if scope == "selected":
        if not isinstance(ids, list) or any(isinstance(value, bool) for value in ids):
            raise HTTPException(400, "账号 ID 无效")
        try:
            ids = sorted({int(value) for value in ids})
        except (TypeError, ValueError):
            raise HTTPException(400, "账号 ID 无效") from None
        if not ids or len(ids) > 200:
            raise HTTPException(400, "请选择 1–200 个账号")
    elif kind == "health" and scope == "all" and payload.get("confirm") is not True:
        raise HTTPException(400, "全部体检需要明确确认")

    async with SessionLocal() as db:
        total, max_account_id = await scope_snapshot(db, scope, ids)
        if total == 0:
            raise HTTPException(400, "当前范围内没有可处理账号")
        job = Job(
            kind=kind,
            scope=scope,
            status="queued",
            priority=100 if scope == "selected" else 40,
            account_ids=ids if scope == "selected" else None,
            options={"max_account_id": max_account_id} if max_account_id else {},
            total=total,
        )
        db.add(job)
        await db.flush()
        await add_audit(
            db,
            f"{kind}_queued",
            {"job_id": job.id, "scope": scope, "total": total},
            hash_ip(client_ip(request)),
        )
        await db.commit()
    return {"ok": True, "job": job_public(job)}


@app.get("/api/jobs")
async def list_jobs(_session: dict = Depends(require_session), limit: int = Query(30, ge=1, le=100)):
    async with SessionLocal() as db:
        rows = list((await db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))).all())
    return {"items": [job_public(row) for row in rows]}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, _session: dict = Depends(require_csrf)):
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        if job.status not in {"queued", "running"}:
            raise HTTPException(400, "该任务已结束")
        job.cancel_requested = True
        await add_audit(db, "job_cancel_requested", {"job_id": job_id}, hash_ip(client_ip(request)))
        await db.commit()
    return {"ok": True}


@app.get("/api/settings")
async def read_settings(_session: dict = Depends(require_session)):
    async with SessionLocal() as db:
        return await get_policy(db)


@app.put("/api/settings")
async def update_settings(request: Request, payload: dict, _session: dict = Depends(require_csrf)):
    interval = payload_integer(payload, "interval_days", 60)
    refresh_concurrency = payload_integer(payload, "refresh_concurrency", 40)
    health_concurrency = payload_integer(payload, "health_concurrency", 8)
    retries = payload_integer(payload, "max_retries", 3)
    schedule_time = str(payload.get("schedule_time", "03:30"))
    timezone_name = str(payload.get("timezone", "Asia/Shanghai"))
    if not 1 <= interval <= 89:
        raise HTTPException(400, "刷新间隔应为 1–89 天")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
        raise HTTPException(400, "定时执行时间格式无效")
    if not 1 <= refresh_concurrency <= 150 or not 1 <= health_concurrency <= 30:
        raise HTTPException(400, "并发数超出安全范围")
    if not 1 <= retries <= 5:
        raise HTTPException(400, "重试次数应为 1–5")
    if len(timezone_name) > 128 or not re.fullmatch(r"[A-Za-z0-9_+\-/]+", timezone_name):
        raise HTTPException(400, "时区名称无效")
    try:
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(400, "时区名称无效") from None
    value = {
        "interval_days": interval,
        "schedule_time": schedule_time,
        "timezone": timezone_name,
        "refresh_concurrency": refresh_concurrency,
        "health_concurrency": health_concurrency,
        "max_retries": retries,
    }
    async with SessionLocal() as db:
        row = await db.get(AppSetting, "refresh_policy")
        if row:
            row.value = value
        else:
            db.add(AppSetting(key="refresh_policy", value=value))
        await add_audit(db, "settings_updated", value, hash_ip(client_ip(request)))
        await db.commit()
    return {"ok": True, "policy": value}


@app.delete("/api/accounts")
async def delete_accounts(request: Request, payload: dict, _session: dict = Depends(require_csrf)):
    if payload.get("confirm") is not True:
        raise HTTPException(400, "删除操作需要明确确认")
    raw_ids = payload.get("ids") or []
    if not isinstance(raw_ids, list) or any(isinstance(value, bool) for value in raw_ids):
        raise HTTPException(400, "账号 ID 无效")
    try:
        ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        raise HTTPException(400, "账号 ID 无效") from None
    if not ids or len(ids) > 200:
        raise HTTPException(400, "一次可删除 1–200 个账号")
    async with SessionLocal() as db:
        result = await db.execute(delete(Account).where(Account.id.in_(ids)))
        count = int(result.rowcount or 0)
        await add_audit(db, "accounts_deleted", {"count": count}, hash_ip(client_ip(request)))
        await db.commit()
    return {"ok": True, "deleted": count}
