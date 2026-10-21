"""평년(과거 10년 같은 시기 평균). 예보 창(오늘부터 16일) 밖의 날에 예보 대신 보여 준다.

- 한 격자(0.1°)의 지난 10년(완결된 해) 일별 기록을 한 번 받아서, 1년 365(366)일 각각의 평년을 한꺼번에
  계산해 저장한다. 같은 격자는 새해가 될 때까지 외부 요청이 없다. (weather.ForecastService 와 같은 구조)
- 어떤 날의 평년은 그 날짜 앞뒤 WINDOW_DAYS 일(7일 창) × 10년의 값으로 낸다. 하루치만 쓰면 한 해의
  우연에 휘둘리고, 창이 넓으면 계절이 번진다.
- 계산은 순수 함수(compute_normals)라서 DB·네트워크 없이 테스트할 수 있다.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable, Protocol, Sequence

from .providers import ProviderError, openmeteo
from .weather import RAIN_DAY_MM, Key, _round, grid_center, grid_key

YEARS = 10
WINDOW_DAYS = 3                  # 그 날짜 앞뒤로 이만큼씩
MIN_SAMPLES = 30                 # 이보다 표본이 적은 날짜는 평년을 만들지 않는다 (기록이 모자란 곳)
COLD_PERCENTILE = 10             # "추운 날": 일 최저기온의 하위 10%
HOT_PERCENTILE = 90              # "따뜻한 날": 일 최고기온의 상위 10%
RAINY_LIKELY_RATIO = 0.4         # 비 온 날 비율이 이 이상이면 "비 올 가능성 높음"(rainLikely)
RETRY_AFTER = 300.0              # 받기가 실패한 격자를 다시 시도하기까지(초). 예보보다 무거운 요청이라 더 길게
MAX_PARALLEL_FETCH = 2

# "MM-DD" → {"tmin", "tmax", "cold", "hot", "rainy"}  (migrations/012_climate_normals.sql 참고)
Normals = dict[str, dict]


def history_range(today: date) -> tuple[date, date]:
    """평년에 쓰는 기간: 오늘 이전의 완결된 YEARS 개 해. (올해는 아직 끝나지 않았고 최근 며칠은 기록이 늦다)"""
    last = today.year - 1
    return date(last - YEARS + 1, 1, 1), date(last, 12, 31)


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """선형 보간 백분위수. 값이 하나뿐이면 그 값."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def compute_normals(history: Iterable[dict]) -> Normals:
    """일별 기록(openmeteo.parse_history 의 모양)으로 날짜(MM-DD)별 평년을 만든다.
    표본이 MIN_SAMPLES 보다 적은 날짜는 빠진다. 2월 29일은 윤년이 아닌 해에는 2월 28일 앞뒤를 쓴다."""
    by_date = {date.fromisoformat(r["date"]): r for r in history}
    years = sorted({d.year for d in by_date})
    out: Normals = {}
    d = date(2024, 1, 1)               # 윤년을 골라 366일을 모두 돈다
    while d.year == 2024:
        tmins: list[float] = []
        tmaxs: list[float] = []
        rains: list[float] = []
        for y in years:
            try:
                center = date(y, d.month, d.day)
            except ValueError:         # 윤년이 아닌 해에는 2월 29일이 없다. 2월 28일 앞뒤로 대신한다
                center = date(y, 2, 28)
            for off in range(-WINDOW_DAYS, WINDOW_DAYS + 1):
                r = by_date.get(center + timedelta(days=off))
                if r is None:
                    continue
                tmins.append(r["tmin"])
                tmaxs.append(r["tmax"])
                if r["rain_mm"] is not None:
                    rains.append(r["rain_mm"])
        if len(tmins) >= MIN_SAMPLES and len(rains) >= MIN_SAMPLES:
            tmins.sort()
            tmaxs.sort()
            out[d.strftime("%m-%d")] = {
                "tmin": round(sum(tmins) / len(tmins), 1),
                "tmax": round(sum(tmaxs) / len(tmaxs), 1),
                "cold": round(_percentile(tmins, COLD_PERCENTILE), 1),
                "hot": round(_percentile(tmaxs, HOT_PERCENTILE), 1),
                "rainy": round(sum(1 for m in rains if m >= RAIN_DAY_MM) / len(rains), 2),
            }
        d += timedelta(days=1)
    return out


def normal_weather(normals: Normals | None, day: date, reason: str) -> dict | None:
    """그날의 평년을 UI(`DayWeather`, source="normal")의 모양으로. 평년이 없으면 None (그 날은 숨긴다).
    reason: 예보 대신 평년을 쓰는 이유. "out_of_range"(예보 기간 밖) | "forecast_failed"(예보를 못 받음)"""
    n = (normals or {}).get(day.strftime("%m-%d"))
    if n is None:
        return None
    return {
        "date": day.isoformat(),
        "source": "normal",
        "confidence": "low",
        "condition": None,
        "tempMin": _round(n["tmin"]),
        "tempMax": _round(n["tmax"]),
        "feelsMin": None,
        "feelsMax": None,
        "windMax": None,
        "rainy": None,
        "rainPeriod": None,
        "precipMm": None,
        "precipProbability": None,
        "sunrise": None,
        "sunset": None,
        "normal": {
            "coldTemp": _round(n["cold"]),
            "hotTemp": _round(n["hot"]),
            "rainyRatio": n["rainy"],
            "rainLikely": n["rainy"] >= RAINY_LIKELY_RATIO,
            "fallbackReason": reason,
        },
    }


class ClimateStore(Protocol):
    async def load(self, keys: Sequence[Key]) -> dict[Key, tuple[int, Normals]]: ...
    async def save(self, key: Key, through_year: int, normals: Normals, now: datetime) -> None: ...


class PgClimateStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def load(self, keys: Sequence[Key]) -> dict[Key, tuple[int, Normals]]:
        if not keys:
            return {}
        rows = await self._pool.fetch(
            """select c.lat10, c.lng10, c.through_year, c.days
                 from climate_normals c
                 join unnest($1::int[], $2::int[]) as k(lat10, lng10)
                   on c.lat10 = k.lat10 and c.lng10 = k.lng10""",
            [k[0] for k in keys], [k[1] for k in keys],
        )
        out: dict[Key, tuple[int, Normals]] = {}
        for r in rows:
            days = r["days"]
            if isinstance(days, str):      # jsonb 코덱이 없으면 문자열로 온다
                days = json.loads(days)
            out[(r["lat10"], r["lng10"])] = (r["through_year"], days)
        return out

    async def save(self, key: Key, through_year: int, normals: Normals, now: datetime) -> None:
        await self._pool.execute(
            """insert into climate_normals (lat10, lng10, through_year, computed_at, days)
               values ($1, $2, $3, $4, $5::jsonb)
               on conflict (lat10, lng10) do update
                 set through_year = excluded.through_year, computed_at = excluded.computed_at, days = excluded.days""",
            key[0], key[1], through_year, now, json.dumps(normals),
        )


FetchHistory = Callable[[float, float, date, date], Awaitable[list[dict]]]


class NormalsService:
    def __init__(
        self,
        store: ClimateStore,
        fetch: FetchHistory = openmeteo.fetch_history,
        *,
        retry_after: float = RETRY_AFTER,
        today: Callable[[], date] = lambda: datetime.now(timezone.utc).date(),
        mono: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store, self._fetch, self._retry_after = store, fetch, retry_after
        self._today, self._mono, self._clock = today, mono, clock
        self._inflight: dict[Key, asyncio.Task[Normals | None]] = {}
        self._failed_until: dict[Key, float] = {}
        self._sem = asyncio.Semaphore(MAX_PARALLEL_FETCH)

    async def normals(self, points: Iterable[tuple[float, float]]) -> dict[Key, Normals | None]:
        """좌표들의 평년. 키는 격자(grid_key). 받을 수 없으면 그 격자는 None."""
        keys = list(dict.fromkeys(grid_key(lat, lng) for lat, lng in points))
        cached = await self._store.load(keys)
        through = history_range(self._today())[1].year

        result: dict[Key, Normals | None] = {}
        to_refresh: list[Key] = []
        for k in keys:
            hit = cached.get(k)
            if hit and hit[0] >= through:
                result[k] = hit[1]
            else:
                to_refresh.append(k)

        fetched = await asyncio.gather(*(self._refresh(k) for k in to_refresh))
        for k, fresh in zip(to_refresh, fetched):
            if fresh is not None:
                result[k] = fresh
            else:
                hit = cached.get(k)
                result[k] = hit[1] if hit else None      # 새로 못 받으면 작년까지 계산한 평년이라도
        return result

    async def _refresh(self, key: Key) -> Normals | None:
        if self._failed_until.get(key, 0.0) > self._mono():
            return None
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch_and_save(key))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _fetch_and_save(self, key: Key) -> Normals | None:
        start, end = history_range(self._today())
        async with self._sem:
            try:
                history = await self._fetch(*grid_center(key), start, end)
            except ProviderError:
                self._failed_until[key] = self._mono() + self._retry_after
                return None
        normals = compute_normals(history)
        if not normals:            # 기록이 비었거나 모자라면 실패로 본다 (저장하면 1년 동안 평년이 없다)
            self._failed_until[key] = self._mono() + self._retry_after
            return None
        try:
            await self._store.save(key, end.year, normals, self._clock())
        except Exception:          # 저장이 안 돼도 방금 계산한 평년은 이번 응답에 쓴다
            pass
        return normals
