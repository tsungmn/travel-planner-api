from typing import Literal

import asyncpg
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field

from ..config import settings
from ..db import get_pool
from ..deps import COOKIE, bearer_scheme, current_user
from ..ratelimit import AttemptLimiter
from ..security import DUMMY_HASH, hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_DAYS = 30

# 로그인 실패 횟수 제한 (계정별 / IP별), 가입 시도 제한 (IP별)
_login_fail_email = AttemptLimiter(limit=10, window_s=15 * 60)
_login_fail_ip = AttemptLimiter(limit=30, window_s=15 * 60)
_register_ip = AttemptLimiter(limit=10, window_s=60 * 60)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


class Register(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=50)


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class DeleteAccount(BaseModel):
    reason: Literal[
        "not_useful", "privacy_concern", "too_difficult", "found_alternative", "other"
    ]
    detail: str | None = Field(default=None, max_length=1000)


async def create_session(user_id) -> str:
    token = new_token()
    await get_pool().execute(
        """insert into sessions (token_hash, user_id, expires_at)
           values ($1, $2, now() + make_interval(days => $3))""",
        hash_token(token), user_id, SESSION_DAYS,
    )
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE, token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=201)
async def register(body: Register, request: Request, response: Response):
    ip = _ip(request)
    if _register_ip.blocked(ip):
        raise HTTPException(429, "too_many_attempts")
    _register_ip.add(ip)
    email = body.email.lower()
    pw_hash = await run_in_threadpool(hash_password, body.password)
    try:
        user_id = await get_pool().fetchval(
            "insert into users (email, password_hash, name) values ($1, $2, $3) returning id",
            email, pw_hash, body.name,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "email_taken")
    token = await create_session(user_id)
    set_session_cookie(response, token)
    return {"id": str(user_id), "email": email, "name": body.name, "token": token}


@router.post("/login")
async def login(body: Login, request: Request, response: Response):
    email_key, ip = f"e:{body.email.lower()}", _ip(request)
    if _login_fail_email.blocked(email_key) or _login_fail_ip.blocked(ip):
        raise HTTPException(429, "too_many_attempts")   # 해시 검증 전에 막아 CPU 낭비도 줄인다
    row = await get_pool().fetchrow(
        "select id, email, name, password_hash from users where email = $1",
        body.email.lower(),
    )
    has_pw = bool(row and row["password_hash"])
    # 계정이 없거나 Google 전용 계정이어도 같은 비용의 검증을 수행 (응답 시간으로 존재 여부가 드러나지 않게)
    verified = await run_in_threadpool(
        verify_password, row["password_hash"] if has_pw else DUMMY_HASH, body.password
    )
    if not (has_pw and verified):
        _login_fail_email.add(email_key)
        _login_fail_ip.add(ip)
        raise HTTPException(401, "invalid_credentials")
    _login_fail_email.clear(email_key)
    token = await create_session(row["id"])
    set_session_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "token": token}


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    # 웹은 쿠키, 앱/Swagger 는 Bearer 헤더. 둘 다 있으면 둘 다 폐기한다.
    tokens = {
        t for t in (creds.credentials.strip() if creds else None, request.cookies.get(COOKIE)) if t
    }
    if tokens:
        await get_pool().execute(
            "delete from sessions where token_hash = any($1::text[])",
            [hash_token(t) for t in tokens],
        )
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return {"id": str(user["id"]), "email": user["email"], "name": user["name"]}


@router.delete("/me", status_code=204)
async def delete_me(
    body: DeleteAccount, response: Response, user: dict = Depends(current_user)
):
    # 사유는 익명으로만 남긴다. (user_id·이메일은 저장하지 않음)
    # users 삭제 시 sessions, trips(및 하위 stops/flights/lodgings/areas), place_usage 가 cascade 로 함께 지워진다.
    detail = (body.detail or "").strip() or None
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "insert into account_deletions (reason, detail) values ($1, $2)",
                body.reason, detail,
            )
            await conn.execute("delete from users where id = $1", user["id"])
    response.delete_cookie(COOKIE, path="/")


async def _upsert_google_user(email: str, sub: str, name: str | None):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("select id from users where google_sub = $1", sub)
            if row:
                return row["id"]
            row = await conn.fetchrow("select id from users where email = $1 for update", email)
            if row:
                # 같은 이메일의 비밀번호 계정을 Google 계정에 연결한다.
                # 이메일 인증 없이 만든 계정을 누가 미리 선점했을 수 있으므로
                # 기존 비밀번호와 세션은 폐기한다.
                await conn.execute(
                    """update users
                          set google_sub = $2, password_hash = null, name = coalesce(name, $3)
                        where id = $1""",
                    row["id"], sub, name,
                )
                await conn.execute("delete from sessions where user_id = $1", row["id"])
                return row["id"]
            return await conn.fetchval(
                "insert into users (email, google_sub, name) values ($1, $2, $3) returning id",
                email, sub, name,
            )


@router.get("/google")
async def google_start(request: Request):
    redirect_uri = f"{settings.public_url}/api/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(request: Request):
    fail = RedirectResponse(f"{settings.public_url}/login?error=auth")
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return fail
    info = token.get("userinfo") or {}
    if not info.get("email") or not info.get("email_verified") or not info.get("sub"):
        return fail

    user_id = await _upsert_google_user(info["email"].lower(), info["sub"], info.get("name"))
    resp = RedirectResponse(f"{settings.public_url}/trips")
    set_session_cookie(resp, await create_session(user_id))
    return resp