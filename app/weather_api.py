"""일차별 위치(weather_location) + 예보(weather) + 평년(climate) + 도시 이름(place_names)을 합쳐 API 응답을 만든다.
순수 함수라서 DB·네트워크 없이 테스트할 수 있다."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Mapping, Sequence

from .climate import Normals, normal_weather
from .providers import openmeteo
from .weather import Forecast, Key, day_weather, grid_key
from .weather_location import DayLocation, _km as km_between

# 이름이 달라도 이 거리 안이면 같은 도시로 본다. 사용자가 고른 도시 이름과 자동으로 알아낸 이름의
# 표기가 다른 경우("Vienna"와 "Wien")에 이동일로 잘못 표시되는 것을 막는다.
SAME_CITY_KM = 10.0


def in_forecast_window(day: date, today: date) -> bool:
    """예보가 있을 수 있는 날인지. 이 밖의 날은 외부 요청을 아예 하지 않는다."""
    return today - timedelta(days=openmeteo.PAST_DAYS) <= day <= today + timedelta(days=openmeteo.FORECAST_DAYS - 1)


def needed_days(start_date: date, num_days: int, today: date) -> list[int]:
    """예보를 받아야 하는 일차. 창 밖의 날은 예보를 요청하지 않는다."""
    return [d for d in range(num_days) if in_forecast_window(start_date + timedelta(days=d), today)]


def visible_days(start_date: date, num_days: int, today: date) -> list[int]:
    """화면에 날씨를 보여 줄 수 있는 일차. 예보 창의 지난 날(PAST_DAYS)보다 더 지난 날은 평년도 의미가 없어서 뺀다."""
    floor = today - timedelta(days=openmeteo.PAST_DAYS)
    return [d for d in range(num_days) if start_date + timedelta(days=d) >= floor]


def lookup_points(locations: Sequence[DayLocation | None], days: Sequence[int]) -> list[tuple[float, float]]:
    """예보·이름을 받을 좌표. 보이는 일차에 더해, 도시 변경 판정에 쓸 그 앞 일차도 포함한다."""
    idx = set(days)
    if days:
        if min(days) > 0:
            idx.add(min(days) - 1)
    return [(l.lat, l.lng) for d in sorted(idx) if (l := locations[d]) is not None]


def _has_forecast(fc: Forecast | None, day: date) -> bool:
    return fc is not None and any(e["date"] == day.isoformat() for e in fc.days)


def normal_points(
    *, start_date: date, locations: Sequence[DayLocation | None],
    forecasts: Mapping[Key, Forecast | None], today: date,
) -> list[tuple[float, float]]:
    """평년이 필요한 좌표: 오늘 이후인데 예보가 없는 날의 위치(와 이동일 표시에 쓸 앞 일차 위치)."""
    pts: list[tuple[float, float]] = []
    for d, loc in enumerate(locations):
        day = start_date + timedelta(days=d)
        if loc is None or day < today:
            continue
        if in_forecast_window(day, today) and _has_forecast(forecasts.get(grid_key(loc.lat, loc.lng)), day):
            continue
        pts.append((loc.lat, loc.lng))
        prev = locations[d - 1] if d > 0 else None
        if prev is not None:
            pts.append((prev.lat, prev.lng))
    return list(dict.fromkeys(pts))


def _summary(days: list[dict], numbers: list[int]) -> dict | None:
    """보이는 날 전체의 요약. numbers: days 와 같은 순서의 1부터 시작하는 일차 번호."""
    if not days:
        return None
    fc = [d for d in days if d["source"] == "forecast"]
    nm = [d for d in days if d["source"] == "normal"]
    basis = "forecast" if not nm else "normal" if not fc else "mixed"
    # 평년 날은 비 온 날 비율을 더해 반올림한다. (예: 0.3 + 0.3 + 0.5 = 1.1 → 1일)
    rainy = sum(1 for d in fc if d["rainy"]) + math.floor(sum(d["normal"]["rainyRatio"] for d in nm) + 0.5)
    first_normal = next((n for d, n in zip(days, numbers) if d["source"] == "normal"), None)
    first_low = next((n for d, n in zip(days, numbers) if d["source"] == "forecast" and d["confidence"] == "low"), None)
    return {
        "basis": basis,
        "tempMin": min(d["tempMin"] for d in days),
        "tempMax": max(d["tempMax"] for d in days),
        "rainyDays": rainy,
        "snowDays": sum(1 for d in fc if d["condition"] == "snow"),
        "maxDiurnalRange": max(d["tempMax"] - d["tempMin"] for d in days),
        "firstNormalDay": first_normal,
        "lowConfidenceFromDay": first_low,
    }


def build_day_weather(
    *,
    start_date: date,
    locations: Sequence[DayLocation | None],
    forecasts: Mapping[Key, Forecast | None],
    normals: Mapping[Key, Normals | None],
    names: Mapping[Key, str | None],
    today: date,
) -> dict:
    """날씨를 보여 줄 수 있는 일차만, 일차 순서대로. 응답 본문(attribution 제외)을 돌려준다.

    일마다 근거를 고른다:
      1. 예보 창 안이고 그날 예보가 있으면 예보(source="forecast")
      2. 아니고 오늘 이후이면 평년(source="normal"). 창 밖이면 "out_of_range", 창 안인데 예보가 없으면 "forecast_failed"
      3. 그것도 없거나 지난 날이면 숨긴다 (위치가 없는 날도)
    placeChangedFromPrev: 앞 일차와 도시 이름이 다르고 SAME_CITY_KM 넘게 떨어져 있으면 True.
      둘 중 하나라도 이름을 모르면 False.
    transitFrom: placeChangedFromPrev인 날에만. 앞 일차 도시의 그날 날씨(예보나 평년). 없으면 None.
    fetchedAt/stale: 실제로 쓴 예보의 가장 오래된 수신 시각과, 그중 낡은 것이 있는지. 예보를 안 썼으면 None/False.
    """
    def name_of(d: int) -> str | None:
        loc = locations[d]
        if loc is None:
            return None
        return loc.name or names.get(grid_key(loc.lat, loc.lng))

    def point(loc: DayLocation, day: date) -> tuple[dict, Forecast | None] | None:
        key = grid_key(loc.lat, loc.lng)
        in_window = in_forecast_window(day, today)
        fc = forecasts.get(key)
        if in_window:
            w = day_weather(fc.days if fc else None, day, today)
            if w is not None:
                return w, fc
        if day >= today:
            w = normal_weather(normals.get(key), day, "forecast_failed" if in_window else "out_of_range")
            if w is not None:
                return w, None
        return None

    out: list[dict] = []
    numbers: list[int] = []
    used: list[Forecast] = []
    for d, loc in enumerate(locations):
        day = start_date + timedelta(days=d)
        if loc is None:
            continue
        got = point(loc, day)
        if got is None:
            continue
        w, fc = got
        if fc is not None:
            used.append(fc)
        name = name_of(d)
        prev_loc = locations[d - 1] if d > 0 else None
        prev = name_of(d - 1) if d > 0 else None
        changed = bool(
            name and prev and name != prev and prev_loc is not None
            and km_between((loc.lat, loc.lng), (prev_loc.lat, prev_loc.lng)) >= SAME_CITY_KM
        )
        transit_from = None
        if changed:
            pw = point(prev_loc, day)
            if pw is not None:
                transit_from = {
                    **pw[0],
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
        numbers.append(d + 1)
    return {
        "days": out,
        "summary": _summary(out, numbers),
        "fetchedAt": min((f.fetched_at for f in used), default=None),
        "stale": any(f.stale for f in used),
    }
