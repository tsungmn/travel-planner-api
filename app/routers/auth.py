import logging
from typing import Literal

import asyncpg
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field

from .. import mailer, turnstile
from ..clientip import client_ip
from ..config import settings
from ..db import get_pool
from ..deps import COOKIE, bearer_scheme, current_user
from ..ratelimit import AttemptLimiter
from ..security import DUMMY_HASH, hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("auth")

SESSION_DAYS = 30

# email_tokens.purpose 값
VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"

# 로그인 실패 횟수 제한 (계정별 / IP별), 가입 시도 제한 (IP별)
_login_fail_email = AttemptLimiter(limit=10, window_s=15 * 60)
_login_fail_ip = AttemptLimiter(limit=30, window_s=15 * 60)
_register_ip = AttemptLimiter(limit=10, window_s=60 * 60)
# 가입 요청도 이메일별로 센다. 이미 있는 이메일에는 안내 메일이 나가므로, 남의 메일함을 채우는 데 쓰지 못하게 한다.
_register_email = AttemptLimiter(limit=5, window_s=30 * 60)

# 메일을 보내는 요청 제한. 계정이 있든 없든 똑같이 센다. (제한 때문에 계정 존재 여부가 드러나지 않게)
_resend_email = AttemptLimiter(limit=5, window_s=30 * 60)
_resend_ip = AttemptLimiter(limit=20, window_s=60 * 60)
_forgot_email = AttemptLimiter(limit=5, window_s=30 * 60)
_forgot_ip = AttemptLimiter(limit=20, window_s=60 * 60)
# 메일 링크의 토큰을 제출하는 요청 (인증, 재설정) 의 IP별 제한. 토큰을 찍어 보는 시도를 막는다.
_token_ip = AttemptLimiter(limit=20, window_s=15 * 60)


def _ip(request: Request) -> str:
    return client_ip(request)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# Cloudflare Turnstile 토큰. 서버에 TURNSTILE_SECRET_KEY 가 있을 때만 검사한다.
TurnstileToken = Field(default=None, max_length=2048)


class Register(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=50)
    turnstile_token: str | None = TurnstileToken


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class VerifyEmail(BaseModel):
    token: str = Field(max_length=200)


class EmailOnly(BaseModel):
    email: EmailStr
    turnstile_token: str | None = TurnstileToken


class ResetPassword(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(min_length=8, max_length=128)


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


async def _issue_token(user_id, purpose: str, ttl_seconds: int) -> str:
    """메일로 보낼 일회용 토큰을 만든다. DB 에는 해시만 저장하고, 같은 목적의 이전 토큰은 지운다."""
    token = new_token()
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "delete from email_tokens where user_id = $1 and purpose = $2", user_id, purpose
            )
            await conn.execute(
                """insert into email_tokens (token_hash, user_id, purpose, expires_at)
                   values ($1, $2, $3, now() + make_interval(secs => $4))""",
                hash_token(token), user_id, purpose, float(ttl_seconds),
            )
    return token


async def _consume_token(conn: asyncpg.Connection, token: str, purpose: str):
    """유효한 토큰이면 지우고(한 번만 쓸 수 있다) user_id 를 돌려준다. 아니면 None."""
    return await conn.fetchval(
        """delete from email_tokens
            where token_hash = $1 and purpose = $2 and expires_at > now()
        returning user_id""",
        hash_token(token), purpose,
    )


async def _send_verification(user_id, email: str) -> None:
    """인증 메일을 만들어 보낸다. 백그라운드에서 실행되므로 실패해도 예외를 밖으로 내지 않는다."""
    try:
        token = await _issue_token(user_id, VERIFY_EMAIL, settings.email_verify_ttl_hours * 3600)
        link = f"{settings.public_url}/verify-email?token={token}"
        subject, text, body = mailer.verification_email(link, settings.email_verify_ttl_hours)
        await mailer.send_email(email, subject, text, body)
    except Exception:
        log.exception("인증 메일 처리 실패")


async def _resend_verification_task(email: str) -> None:
    # 계정이 없거나 이미 인증했으면 아무것도 보내지 않는다. (응답은 어느 경우든 같다)
    try:
        row = await get_pool().fetchrow(
            "select id, email_verified_at from users where email = $1", email
        )
        if row and row["email_verified_at"] is None:
            await _send_verification(row["id"], email)
    except Exception:
        log.exception("인증 메일 재전송 처리 실패")


async def _notify_existing_account_task(email: str) -> None:
    """이미 가입된 이메일로 가입을 시도하면 안내만 보낸다. 인증 링크는 보내지 않는다.
    (누가 남의 이메일로 비밀번호를 정해 두고 미인증 계정을 선점해 둔 경우, 진짜 주인이 그 링크로 인증하면
    선점한 사람이 아는 비밀번호로 계정이 열린다. 진짜 주인은 비밀번호 재설정으로 계정을 되찾는다.)"""
    try:
        subject, text, body = mailer.account_exists_email(
            f"{settings.public_url}/login", f"{settings.public_url}/forgot-password"
        )
        await mailer.send_email(email, subject, text, body)
    except Exception:
        log.exception("기존 계정 안내 메일 처리 실패")


async def _send_password_reset_task(email: str) -> None:
    try:
        row = await get_pool().fetchrow("select id from users where email = $1", email)
        if not row:
            return
        token = await _issue_token(row["id"], RESET_PASSWORD, settings.password_reset_ttl_minutes * 60)
        link = f"{settings.public_url}/reset-password?token={token}"
        subject, text, body = mailer.password_reset_email(link, settings.password_reset_ttl_minutes)
        await mailer.send_email(email, subject, text, body)
    except Exception:
        log.exception("비밀번호 재설정 메일 처리 실패")


def _limit_mail_request(email_limiter: AttemptLimiter, ip_limiter: AttemptLimiter, email: str, ip: str) -> None:
    key = f"m:{email}"
    if email_limiter.blocked(key) or ip_limiter.blocked(ip):
        raise HTTPException(429, "too_many_attempts")
    email_limiter.add(key)
    ip_limiter.add(ip)


@router.post("/register", status_code=201)
async def register(body: Register, request: Request, background: BackgroundTasks):
    ip = _ip(request)
    if _register_ip.blocked(ip):
        raise HTTPException(429, "too_many_attempts")
    _register_ip.add(ip)
    await turnstile.require(body.turnstile_token, ip)
    email = body.email.lower()
    key = f"r:{email}"
    if _register_email.blocked(key):
        raise HTTPException(429, "too_many_attempts")
    _register_email.add(key)
    # 해시는 이메일이 이미 있어도 똑같이 계산한다. (응답 시간으로 가입 여부가 드러나지 않게)
    pw_hash = await run_in_threadpool(hash_password, body.password)
    try:
        user_id = await get_pool().fetchval(
            "insert into users (email, password_hash, name) values ($1, $2, $3) returning id",
            email, pw_hash, body.name,
        )
    except asyncpg.UniqueViolationError:
        # 이미 가입된 이메일이어도 응답은 같다. (가입 여부를 알려 주지 않기 위해) 주인에게만 안내 메일을 보낸다.
        background.add_task(_notify_existing_account_task, email)
        return {"email": email}
    # 이메일을 인증하기 전에는 로그인 세션을 만들지 않는다. 인증 메일은 응답 후에 보낸다.
    background.add_task(_send_verification, user_id, email)
    return {"email": email}


@router.post("/verify-email", status_code=204)
async def verify_email(body: VerifyEmail, request: Request):
    ip = _ip(request)
    if _token_ip.blocked(ip):
        raise HTTPException(429, "too_many_attempts")
    _token_ip.add(ip)
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            user_id = await _consume_token(conn, body.token, VERIFY_EMAIL)
            if user_id is None:
                raise HTTPException(400, "invalid_or_expired_token")
            await conn.execute(
                "update users set email_verified_at = coalesce(email_verified_at, now()) where id = $1",
                user_id,
            )


@router.post("/resend-verification", status_code=204)
async def resend_verification(body: EmailOnly, request: Request, background: BackgroundTasks):
    # 가입된 이메일인지 알려주지 않기 위해 항상 204. 실제 조회와 발송은 응답 후에 한다.
    email = body.email.lower()
    ip = _ip(request)
    _limit_mail_request(_resend_email, _resend_ip, email, ip)
    await turnstile.require(body.turnstile_token, ip)
    background.add_task(_resend_verification_task, email)


@router.post("/forgot-password", status_code=204)
async def forgot_password(body: EmailOnly, request: Request, background: BackgroundTasks):
    email = body.email.lower()
    ip = _ip(request)
    _limit_mail_request(_forgot_email, _forgot_ip, email, ip)
    await turnstile.require(body.turnstile_token, ip)
    background.add_task(_send_password_reset_task, email)


@router.post("/reset-password", status_code=204)
async def reset_password(body: ResetPassword, request: Request):
    ip = _ip(request)
    if _token_ip.blocked(ip):
        raise HTTPException(429, "too_many_attempts")
    _token_ip.add(ip)
    pw_hash = await run_in_threadpool(hash_password, body.password)
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            user_id = await _consume_token(conn, body.token, RESET_PASSWORD)
            if user_id is None:
                raise HTTPException(400, "invalid_or_expired_token")
            # 메일 링크를 열 수 있었다는 것 자체가 이메일 소유 증명이므로 인증도 함께 처리한다.
            await conn.execute(
                """update users
                      set password_hash = $2, email_verified_at = coalesce(email_verified_at, now())
                    where id = $1""",
                user_id, pw_hash,
            )
            # 비밀번호를 바꿨다면 기존 로그인은 모두 끊는다. 남은 링크 토큰도 폐기한다.
            await conn.execute("delete from sessions where user_id = $1", user_id)
            await conn.execute("delete from email_tokens where user_id = $1", user_id)


@router.post("/login")
async def login(body: Login, request: Request, response: Response):
    email_key, ip = f"e:{body.email.lower()}", _ip(request)
    if _login_fail_email.blocked(email_key) or _login_fail_ip.blocked(ip):
        raise HTTPException(429, "too_many_attempts")   # 해시 검증 전에 막아 CPU 낭비도 줄인다
    row = await get_pool().fetchrow(
        "select id, email, name, password_hash, email_verified_at from users where email = $1",
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
    # 비밀번호가 맞은 뒤에만 알려 준다. (틀린 비밀번호로는 계정 상태를 알 수 없다)
    if row["email_verified_at"] is None:
        raise HTTPException(403, "email_not_verified")
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
                # 이메일 인증 없이 만든 계정(이 기능 이전에 가입해 일괄로 인증 처리된 계정 포함)을
                # 누가 미리 선점했을 수 있으므로 기존 비밀번호, 세션, 메일 링크는 폐기한다.
                # Google 이 이메일을 확인해 주었으므로 인증 완료로 처리한다.
                # (비밀번호가 필요하면 "비밀번호를 잊으셨나요?"로 다시 정할 수 있다)
                await conn.execute(
                    """update users
                          set google_sub = $2, password_hash = null, name = coalesce(name, $3),
                              email_verified_at = coalesce(email_verified_at, now())
                        where id = $1""",
                    row["id"], sub, name,
                )
                await conn.execute("delete from sessions where user_id = $1", row["id"])
                await conn.execute("delete from email_tokens where user_id = $1", row["id"])
                return row["id"]
            return await conn.fetchval(
                """insert into users (email, google_sub, name, email_verified_at)
                   values ($1, $2, $3, now()) returning id""",
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