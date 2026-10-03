from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_pool
from .security import hash_token

COOKIE = "sid"

# Swagger UI 에 Authorize 버튼을 만들어 주는 스킴. 헤더가 없어도 에러를 내지 않고 쿠키로 넘어간다.
bearer_scheme = HTTPBearer(auto_error=False)


async def current_user_optional(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    # 앱/Swagger 는 Authorization: Bearer, 웹은 쿠키. 같은 세션 토큰을 쓴다.
    # Bearer 를 먼저 보는 이유: 오래된 sid 쿠키가 남아 있어도 Authorize 로 넣은 토큰이 우선하도록.
    token = creds.credentials.strip() if creds else None
    if not token:
        token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = await get_pool().fetchrow(
        """select u.id, u.email, u.name
             from sessions s join users u on u.id = s.user_id
            where s.token_hash = $1 and s.expires_at > now()""",
        hash_token(token),
    )
    return dict(row) if row else None


async def current_user(user: dict | None = Depends(current_user_optional)) -> dict:
    if not user:
        raise HTTPException(401, "unauthorized")
    return user


async def require_trip(trip_id: UUID, user: dict = Depends(current_user)) -> asyncpg.Record:
    """내 여행만 통과. 남의 여행이거나 없으면 404 (존재 여부를 알려주지 않음).
    num_days = 여행 일수 (day_index 는 0 ~ num_days-1)."""
    row = await get_pool().fetchrow(
        """select *, (end_date - start_date) + 1 as num_days
             from trips where id = $1 and owner_id = $2""",
        trip_id, user["id"],
    )
    if not row:
        raise HTTPException(404, "not_found")
    return row