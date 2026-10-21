"""여행 하나의 날씨 응답을 만드는 흐름: 위치 → (예보 · 이름) → 평년 → 조립.

라우터에서 떼어 낸 이유는 DB 없이 흐름 전체를 테스트하려는 것이다. 서비스는 주입받는다:
  forecast.forecasts(points), normals.normals(points), names.names(points)
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Sequence

from .weather_api import build_day_weather, lookup_points, needed_days, normal_points, visible_days
from .weather_location import DayLocation


async def compose_trip_weather(
    *,
    start_date: date,
    locations: Sequence[DayLocation | None],
    forecast: Any,
    normals: Any,
    names: Any,
    today: date,
) -> dict:
    """응답 본문(attribution 제외). 외부 요청은 필요한 것만 한다:
      - 예보: 예보 창 안의 날의 위치만
      - 평년: 예보가 없는 오늘 이후의 날의 위치만 (예보를 받은 뒤에 정한다)
      - 이름: 보이는 날(평년 날 포함) 전부. 사용자가 이름을 정한 날은 조회하지 않는다
    지난 여행이거나 위치가 하나도 없으면 외부 요청이 없다."""
    n = len(locations)
    fpoints = lookup_points(locations, needed_days(start_date, n, today))
    name_points = lookup_points(locations, visible_days(start_date, n, today))
    name_points = [p for p in name_points if not any(l and l.name and (l.lat, l.lng) == p for l in locations)]
    names_task = asyncio.ensure_future(names.names(name_points))
    try:
        forecasts = await forecast.forecasts(fpoints)
        npoints = normal_points(start_date=start_date, locations=locations, forecasts=forecasts, today=today)
        normal_map = await normals.normals(npoints)
        place_names = await names_task
    finally:
        names_task.cancel()
    return build_day_weather(
        start_date=start_date, locations=locations, forecasts=forecasts,
        normals=normal_map, names=place_names, today=today,
    )
