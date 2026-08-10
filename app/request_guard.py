from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import require_csrf
from .config import settings

ASGIApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
    Awaitable[None],
]


def request_body_limit(path: str) -> int:
    if path == "/login":
        return 4 * 1024
    if path == "/api/import/paste":
        return min(10 * 1024 * 1024, settings.max_upload_bytes) + 64 * 1024
    if path == "/api/import":
        return settings.max_upload_bytes + 64 * 1024
    return 128 * 1024


class RequestGuardMiddleware:
    """Authenticate protected mutations and bound bodies before parser allocation."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        method = str(scope.get("method", "GET")).upper()
        if scope.get("type") != "http" or method not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        limit = request_body_limit(path)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                await JSONResponse({"detail": "Content-Length 无效"}, status_code=400)(scope, receive, send)
                return
            if declared < 0 or declared > limit:
                await JSONResponse({"detail": "请求正文超过安全上限"}, status_code=413)(scope, receive, send)
                return

        if path.startswith("/api/"):
            try:
                require_csrf(Request(scope))
            except HTTPException as exc:
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
                return

        chunks: list[bytes] = []
        received = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    await JSONResponse(
                        {"detail": "请求正文超过安全上限"}, status_code=413
                    )(scope, receive, send)
                    return
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break

        replayed = False

        async def replay_receive() -> dict:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)
