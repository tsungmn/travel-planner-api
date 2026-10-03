import asyncpg
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from ..config import settings
from ..db import get_pool
from ..deps import COOKIE, current_user
from ..security import DUMMY_HASH, hash_password, hash_token, new_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_DAYS = 30

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
async def register(body: Register, response: Response):
    email = body.email.lower()
    pw_hash = await run_in_threadpool(hash_password, body.password)
    try:
        user_id = await get_pool().fetchval(
            "insert into users (email, password_hash, name) values ($1, $2, $3) returning id",
            email, pw_hash, body.name,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "email_taken")
    set_session_cookie(response, await create_session(user_id))
    return {"id": str(user_id), "email": email, "name": body.name}


@router.post("/login")
async def login(body: Login, response: Response):
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
        raise HTTPException(401, "invalid_credentials")
    set_session_cookie(response, await create_session(row["id"]))
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"]}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        await get_pool().execute("delete from sessions where token_hash = $1", hash_token(token))
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return {"id": str(user["id"]), "email": user["email"], "name": user["name"]}


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