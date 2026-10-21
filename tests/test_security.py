"""보안 헤더, 요청 본문 크기 제한, 봇 방지(Turnstile) 검사."""
import asyncio

import httpx
import pytest
from starlette.responses import JSONResponse

from app import turnstile
from app.config import settings
from app.middleware import BodyLimitMiddleware, SecurityHeadersMiddleware


# ---------------------------------------------------------------- DB 없이: 미들웨어 단독

async def _echo_app(scope, receive, send):
    """본문을 끝까지 읽어서 길이를 돌려준다."""
    size = 0
    while True:
        msg = await receive()
        size += len(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    await JSONResponse({"size": size})(scope, receive, send)


def _call(app, **kw):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/x", **kw)
    return asyncio.run(go())


@pytest.mark.no_db
def test_security_headers_on_every_response():
    r = _call(SecurityHeadersMiddleware(_echo_app, hsts=True, csp=True), content=b"{}")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "max-age=" in r.headers["strict-transport-security"]


@pytest.mark.no_db
def test_headers_are_optional_for_dev_and_docs():
    r = _call(SecurityHeadersMiddleware(_echo_app, hsts=False, csp=False), content=b"{}")
    assert "content-security-policy" not in r.headers and "strict-transport-security" not in r.headers
    assert r.headers["cache-control"] == "no-store"      # 이건 항상 붙는다


@pytest.mark.no_db
def test_body_limit_by_content_length_and_by_streaming():
    app = BodyLimitMiddleware(_echo_app, max_bytes=1000)
    assert _call(app, content=b"x" * 1000).json() == {"size": 1000}
    r = _call(app, content=b"x" * 1001)
    assert r.status_code == 413 and r.json() == {"detail": "payload_too_large"}

    async def chunks(n):                                  # Content-Length 가 없는 chunked 요청
        for _ in range(n):
            yield b"x" * 400
    assert _call(app, content=chunks(2)).json() == {"size": 800}
    assert _call(app, content=chunks(3)).status_code == 413


# ---------------------------------------------------------------- DB 필요: 실제 앱

def test_real_app_rejects_a_huge_body_and_keeps_headers(client):
    r = client.post("/api/auth/login", content=b"x" * 300_000, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/healthz").headers["cache-control"] == "no-store"


@pytest.fixture()
def captcha(monkeypatch):
    """Turnstile 을 켜고, 토큰 'good' 만 통과시킨다. (실제 Cloudflare 서버로는 요청하지 않는다)"""
    monkeypatch.setattr(settings, "turnstile_secret_key", "test-secret")
    calls = []

    async def fake(secret, token, ip):
        calls.append((secret, token, ip))
        return token == "good"

    monkeypatch.setattr(turnstile, "_siteverify", fake)
    return calls


def test_captcha_is_off_without_a_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret_key", "")
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"})
    assert r.status_code == 201


def test_register_requires_a_valid_captcha_when_enabled(client, captcha):
    body = {"email": "a@example.com", "password": "password123"}
    for extra in ({}, {"turnstile_token": "bad"}):
        r = client.post("/api/auth/register", json=body | extra)
        assert r.status_code == 400 and r.json() == {"detail": "captcha_failed"}
    r = client.post("/api/auth/register", json=body | {"turnstile_token": "good"})
    assert r.status_code == 201
    assert captcha[-1][:2] == ("test-secret", "good")


@pytest.mark.parametrize("path", ["/api/auth/forgot-password", "/api/auth/resend-verification"])
def test_mail_endpoints_require_a_valid_captcha_when_enabled(client, captcha, outbox, path):
    assert client.post(path, json={"email": "a@example.com"}).status_code == 400
    assert client.post(path, json={"email": "a@example.com", "turnstile_token": "bad"}).status_code == 400
    assert client.post(path, json={"email": "a@example.com", "turnstile_token": "good"}).status_code == 204


def test_captcha_verification_failure_blocks_instead_of_letting_bots_through(client, monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret_key", "test-secret")

    async def boom(*a, **k):
        raise RuntimeError("cloudflare unreachable")

    class Broken:
        async def __aenter__(self): raise RuntimeError("down")
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(turnstile.httpx, "AsyncClient", lambda **k: Broken())
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123", "turnstile_token": "x"})
    assert r.status_code == 400 and r.json() == {"detail": "captcha_failed"}
