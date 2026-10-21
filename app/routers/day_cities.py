"""일차별 날씨 도시를 사용자가 직접 정하는 기능: 도시 검색, 저장, 되돌리기.

정하지 않은 일차는 weather_location 이 일정에서 추정한다. 여기서 저장한 도시는 그 추정보다 먼저 쓴다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_pool
from ..deps import current_user, require_trip
from ..providers import ProviderError, openmeteo
from ..ratelimit import AttemptLimiter
from ..schemas import CitySearchOut, DayCitiesSet

router = APIRouter(prefix="/api", tags=["day-cities"])

LANGS = {"ko", "en", "zh", "ja"}
# 도시 검색은 입력할 때마다 나가는 요청이라 사용자당 분당 횟수만 안전망으로 막는다.
_search_limit = AttemptLimiter(limit=60, window_s=60)


@router.get("/cities/search", response_model=CitySearchOut)
async def search_cities(
    q: str = Query(min_length=1, max_length=100),     # 한 글자 도시("빈")도 있다. 한 글자는 정확히 일치하는 도시만 나온다
    lang: str = "ko",
    user: dict = Depends(current_user),
):
    q = q.strip()
    if not q:
        raise HTTPException(400, "bad_request")
    key = str(user["id"])
    if _search_limit.blocked(key):
        raise HTTPException(429, "rate_limited")
    _search_limit.add(key)
    try:
        items = await openmeteo.search_cities(q, lang if lang in LANGS else "ko")
    except ProviderError:
        raise HTTPException(502, "upstream_error") from None
    return {"items": items, "attribution": "City search by Open-Meteo.com"}


@router.put("/trips/{trip_id}/day-cities", status_code=204)
async def set_day_cities(body: DayCitiesSet, trip=Depends(require_trip)):
    if body.to_day >= trip["num_days"]:
        raise HTTPException(422, "day_out_of_range")
    # 일차마다 한 줄. 이미 정한 일차는 새 도시로 바꾼다.
    await get_pool().execute(
        """insert into trip_day_cities (trip_id, day_index, name, lat, lng)
           select $1, d, $4, $5, $6 from generate_series($2::int, $3::int) as d
           on conflict (trip_id, day_index)
           do update set name = excluded.name, lat = excluded.lat, lng = excluded.lng""",
        trip["id"], body.from_day, body.to_day, body.name, body.lat, body.lng,
    )


@router.delete("/trips/{trip_id}/day-cities", status_code=204)
async def clear_day_cities(
    from_day: int = Query(ge=0, le=60),
    to_day: int = Query(ge=0, le=60),
    trip=Depends(require_trip),
):
    """from_day ~ to_day 의 지정을 지우고 자동 추정으로 되돌린다. 지정이 없던 일차가 섞여 있어도 괜찮다."""
    if to_day < from_day:
        raise HTTPException(422, "validation_error")
    await get_pool().execute(
        "delete from trip_day_cities where trip_id = $1 and day_index between $2 and $3",
        trip["id"], from_day, to_day,
    )
