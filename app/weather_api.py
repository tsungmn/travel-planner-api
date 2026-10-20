"""일차별 위치(weather_location) + 예보(weather) + 도시 이름(place_names)을 합쳐 API 응답을 만든다.
순수 함수라서 DB·네트워크 없이 테스트할 수 있다."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping, Sequence

from .providers import openmeteo
from .weather import Days, Key, day_weather, grid_key
from .weather_location import DayLocation, _km as km_between

# 이름이 달라도 이 거리 안이면 같은 도시로 본다. 사용자가 고른 도시 이름과 자동으로 알아낸 이름의
# 표기가 다른 경우("Vienna"와 "Wien")에 이동일로 잘못 표시되는 것을 막는다.
SAME_CITY_KM = 10.0


def in_forecast_window(day: date, today: date) -> bool:
    """예보가 있을 수 있는 날인지. 이 밖의 날은 외부 요청을 아예 하지 않는다."""
    return today - timedelta(days=openmeteo.PAST_DAYS) <= day <= today + timedelta(days=openmeteo.FORECAST_DAYS - 1)


def needed_days(start_date: date, num_days: int, today: date) -> list[int]:
    return [d for d in range(num_days) if in_forecast_window(start_date + timedelta(days=d), today)]


def lookup_points(locations: Sequence[DayLocation | None], days: Sequence[int]) -> list[tuple[float, float]]:
    """예보를 받을 좌표. 보이는 일차에 더해, 도시 변경 판정에 쓸 그 앞 일차도 포함한다."""
    idx = set(days)
    if days:
        if min(days) > 0:
            idx.add(min(days) - 1)
    return [(l.lat, l.lng) for d in sorted(idx) if (l := locations[d]) is not None]


def build_day_weather(
    *,
    start_date: date,
    locations: Sequence[DayLocation | None],
    forecasts: Mapping[Key, Days | None],
    names: Mapping[Key, str | None],
    today: date,
) -> list[dict]:
    """날씨를 보여 줄 수 있는 일차만, 일차 순서대로. (위치나 예보가 없는 날은 빠진다)

    placeChangedFromPrev: 앞 일차와 도시 이름이 다르고 SAME_CITY_KM 넘게 떨어져 있으면 True.
      둘 중 하나라도 이름을 모르면 False.
    transitFrom: placeChangedFromPrev인 날에만. 앞 일차 도시의 그날 예보. 예보가 없으면 None.
    """
    def name_of(d: int) -> str | None:
        loc = locations[d]
        if loc is None:
            return None
        return loc.name or names.get(grid_key(loc.lat, loc.lng))

    out: list[dict] = []
    for d, loc in enumerate(locations):
        day = start_date + timedelta(days=d)
        if loc is None or not in_forecast_window(day, today):
            continue
        w = day_weather(forecasts.get(grid_key(loc.lat, loc.lng)), day, today)
        if w is None:
            continue
        name = name_of(d)
        prev_loc = locations[d - 1] if d > 0 else None
        prev = name_of(d - 1) if d > 0 else None
        changed = bool(
            name and prev and name != prev and prev_loc is not None
            and km_between((loc.lat, loc.lng), (prev_loc.lat, prev_loc.lng)) >= SAME_CITY_KM
        )
        transit_from = None
        if changed:
            pw = day_weather(forecasts.get(grid_key(prev_loc.lat, prev_loc.lng)), day, today)
            if pw is not None:
                transit_from = {
                    **pw,
                    "placeName": prev,
                    "placeSource": "user" if prev_loc.source == "user" else "auto",
                    "placeChangedFromPrev": False,
                }
        out.append({
            **w,
            "placeName": name,
            "placeSource": "user" if loc.source == "user" else "auto",
            "placeChangedFromPrev": changed,
            "transitFrom": transit_from,
        })
    return out
