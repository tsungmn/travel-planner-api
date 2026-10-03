from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import settings
from ..db import get_pool
from ..deps import current_user
from ..providers import ProviderError, geoapify, nominatim

router = APIRouter(prefix="/api/places", tags=["places"])

SEARCH_LIMIT = 500                  # 사용자당 일일 검색 횟수 (suggest + full 합산)
LANGS = {"ko", "en", "zh", "ja"}    # ISO 639-1
NEAR_RADIUS_M = 50_000              # nearby=true 일 때 여행지 중심 반경
NEAR_SPAN_DEG = 0.45                # Nominatim viewbox 반폭 (약 50km)


class PlaceResult(BaseModel):
    provider: str
    provider_place_id: str
    name: str
    address_text: str | None
    lat: float
    lng: float


class PlaceSearchOut(BaseModel):
    items: list[PlaceResult]
    attribution: str                # 검색 결과를 보여주는 화면에 표시


async def _bump_usage(user_id, limit: int) -> bool:
    n = await get_pool().fetchval(
        """insert into place_usage (user_id, day, kind, n) values ($1, current_date, 'search', 1)
           on conflict (user_id, day, kind) do update set n = place_usage.n + 1
           returning n""",
        user_id,
    )
    return n <= limit


@router.get("/search", response_model=PlaceSearchOut)
async def search(
    q: str = Query(min_length=2, max_length=100),
    mode: Literal["suggest", "full"] = "suggest",   # suggest: 입력 중 제안 / full: 검색 확정(Enter, 버튼)
    lang: str | None = None,                        # 표시 언어. 한국어 화면이면 ko 권장
    lat: float | None = Query(default=None, ge=-90, le=90),     # 여행지 중심 (trip.dest_lat/dest_lng)
    lng: float | None = Query(default=None, ge=-180, le=180),
    nearby: bool = True,                            # true: 여행지 반경 안에서만 검색, false: 전 세계
    user: dict = Depends(current_user),
):
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "bad_request")
    if mode == "suggest" and not settings.geoapify_api_key:
        raise HTTPException(503, "search_not_configured")
    if not await _bump_usage(user["id"], SEARCH_LIMIT):
        raise HTTPException(429, "rate_limited")

    lang = lang if lang in LANGS else None
    try:
        if mode == "suggest":
            items = await geoapify.suggest(q, lang, lat, lng, NEAR_RADIUS_M if nearby else None)
            attribution = geoapify.ATTRIBUTION
        else:
            items = await nominatim.search(q, lang, lat, lng, NEAR_SPAN_DEG if nearby else None)
            attribution = nominatim.ATTRIBUTION
    except ProviderError:
        raise HTTPException(502, "upstream_error")
    return {"items": items, "attribution": attribution}