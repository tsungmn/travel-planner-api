"""모든 응답에 보안 헤더를 붙이고, 너무 큰 요청 본문을 막는 ASGI 미들웨어."""
import json

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 이 API 의 가장 큰 요청(장소 순서 저장 500건)도 100KB 를 넘지 않는다.
MAX_BODY_BYTES = 256 * 1024


class SecurityHeadersMiddleware:
    """API 응답은 JSON 뿐이라 브라우저가 해석하거나 저장할 이유가 없다.

    - no-store: 로그인 응답의 토큰, 여행 데이터가 브라우저/중간 캐시에 남지 않게 한다.
    - nosniff, no-referrer, frame-ancestors 'none': 다른 형식으로 해석하거나 프레임에 넣는 것을 막는다.
    - HSTS: https 로만 서비스하는 운영에서만 (COOKIE_SECURE 가 켜진 환경).
    - /api/docs (Swagger UI) 는 스크립트가 필요하므로 CSP 를 붙이지 않는다. (운영에서는 꺼져 있다)
    """

    def __init__(self, app: ASGIApp, *, hsts: bool, csp: bool) -> None:
        self.app = app
        self.hsts = hsts
        self.csp = csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("Cache-Control", "no-store")
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "no-referrer")
                h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                if self.csp:
                    h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                if self.hsts:
                    h.setdefault("Strict-Transport-Security", "max-age=31536000")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """요청 본문이 한도를 넘으면 413. Content-Length 가 없는(chunked) 요청도 읽는 동안 센다."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = False
            if too_big:
                return await self._reject(send)

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"detail": "payload_too_large"}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
