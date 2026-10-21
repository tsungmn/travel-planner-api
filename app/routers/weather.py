from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..climate import NormalsService, PgClimateStore
from ..db import get_pool
from ..deps import require_trip
from ..place_names import PgNameStore, PlaceNameService
from ..providers import geoapify, openmeteo
from ..schemas import TripWeatherOut
from ..sharing import hide_lodging_stops
from ..trip_weather import compose_trip_weather
from ..weather import ForecastService, PgWeatherStore
from ..weather_location import resolve_day_locations
from .trips import _load_children

router = APIRouter(prefix="/api", tags=["weather"])

# 서비스는 요청 사이에 캐시 상태(진행 중인 요청, 실패 백오프)를 나눠 가져야 해서 프로세스당 하나만 둔다.
# 풀이 바뀌면(서버 재시작, 테스트) 새로 만든다.
_cache: dict = {}


def _services() -> tuple[ForecastService, NormalsService, PlaceNameService]:
    pool = get_pool()
    if _cache.get("pool") is not pool:
        # 모듈 속성을 호출 시점에 찾도록 람다로 감싼다 (테스트에서 바꿔 끼울 수 있게)
        _cache.update(
            pool=pool,
            forecast=ForecastService(PgWeatherStore(pool), lambda lat, lng: openmeteo.fetch_daily(lat, lng)),
            normals=NormalsService(PgClimateStore(pool), lambda lat, lng, start, end: openmeteo.fetch_history(lat, lng, start, end)),
            names=PlaceNameService(PgNameStore(pool), lambda lat, lng: geoapify.reverse_city(lat, lng)),
        )
    return _cache["forecast"], _cache["normals"], _cache["names"]


async def _load_overrides(trip_id, num_days: int) -> dict[int, tuple[float, float, str]]:
    """사용자가 직접 정한 일차별 도시. 여행 기간 밖의 일차(여행을 줄인 경우)는 무시한다."""
    rows = await get_pool().fetch(
        "select day_index, lat, lng, name from trip_day_cities where trip_id = $1 and day_index < $2",
        trip_id, num_days,
    )
    return {r["day_index"]: (r["lat"], r["lng"], r["name"]) for r in rows}


async def _weather_for(trip, stops: list[dict], areas: list[dict], lodgings: list[dict]) -> dict:
    n = (trip["end_date"] - trip["start_date"]).days + 1
    overrides = await _load_overrides(trip["id"], n)
    dest = (trip["dest_lat"], trip["dest_lng"]) if trip["dest_lat"] is not None and trip["dest_lng"] is not None else None
    locations = resolve_day_locations(
        start_date=trip["start_date"], num_days=n, stops=stops, areas=areas, lodgings=lodgings,
        destination=dest, overrides=overrides,
    )
    forecast, normals, names = _services()
    body = await compose_trip_weather(
        start_date=trip["start_date"], locations=locations, forecast=forecast, normals=normals, names=names,
        today=datetime.now(timezone.utc).date(),
    )
    return {**body, "attribution": openmeteo.ATTRIBUTION}


@router.get("/trips/{trip_id}/weather", response_model=TripWeatherOut)
async def trip_weather(trip=Depends(require_trip)):
    flights, lodgings, stops, areas = await _load_children(trip["id"])
    return await _weather_for(trip, stops, areas, lodgings)


@router.get("/shared/{token}/weather", response_model=TripWeatherOut)
async def shared_trip_weather(token: str):
    if len(token) > 100:
        raise HTTPException(404, "not_found")
    trip = await get_pool().fetchrow(
        """select id, dest_lat, dest_lng, start_date, end_date, share_lodgings
             from trips where share_token = $1""",
        token,
    )
    if not trip:
        raise HTTPException(404, "not_found")
    _, lodgings, stops, areas = await _load_children(trip["id"])
    if not trip["share_lodgings"]:
        # 숙소를 공개하지 않으면 숙소 위치로 그날 도시를 정하지 않는다. (도시 이름으로 숙소 위치가 새지 않게)
        stops = [s for s in hide_lodging_stops(stops, lodgings) if not s.get("hidden")]
        lodgings = []
    return await _weather_for(trip, stops, areas, lodgings)
