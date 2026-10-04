from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

import logging
from . import db
from .config import settings
from .providers import geoapify, nominatim
from .routers import auth, flights, lodgings, places, stops, trips


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_pool()
    await db.get_pool().execute("delete from sessions where expires_at < now()")  # 만료 세션 정리
    yield
    await geoapify.close()
    await nominatim.close()
    await db.close_pool()


app = FastAPI(
    title="travel-planner-api",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.docs_enabled else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.docs_enabled else None,
)

# Google OAuth state 저장용 (우리 로그인 세션 쿠키 'sid'와는 별개)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.cookie_secure,
    same_site="lax",
    max_age=600,
)


@app.exception_handler(asyncpg.CheckViolationError)
async def check_violation_handler(_: Request, __: asyncpg.CheckViolationError):
    return JSONResponse({"detail": "constraint_violation"}, status_code=422)


app.include_router(auth.router)
app.include_router(places.router)
app.include_router(trips.router)
app.include_router(stops.router)
app.include_router(flights.router)
app.include_router(lodgings.router)


logging.getLogger("httpx").setLevel(logging.WARNING)


@app.get("/api/healthz")    # liveness: DB 없이 프로세스만 확인
async def healthz():
    return {"ok": True}


@app.get("/api/readyz")     # readiness: DB 연결 확인
async def readyz():
    await db.get_pool().fetchval("select 1")
    return {"ok": True}


@app.get("/api/health")     # 기존 경로 호환
async def health():
    return await readyz()