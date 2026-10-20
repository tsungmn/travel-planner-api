"""날씨 예보 캐시와 조회. 위치 추정(weather_location)이 정한 좌표를 받아 예보를 돌려준다.

- 좌표는 0.1° 격자로 묶는다. 같은 격자는 한 번만 받아 저장하고, 여러 여행이 나눠 쓴다.
- 저장한 지 TTL(6시간)이 지나면 다음 요청 때 새로 받는다. 새로 받기가 실패하면 낡은 예보라도 쓴다.
- 새로 받기가 실패한 격자는 잠깐(RETRY_AFTER) 다시 시도하지 않는다. (외부 서버가 죽었을 때 요청마다 기다리지 않도록)
- DB 접근(WeatherStore)과 외부 요청(fetch)을 주입받아서 DB·네트워크 없이 테스트할 수 있다.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable, Mapping, Protocol, Sequence

from .providers import ProviderError, openmeteo

GRID = 10                              # 격자 크기 = 1/GRID 도
TTL = timedelta(hours=6)
RETRY_AFTER = 60.0                     # 새로 받기가 실패한 격자를 다시 시도하기까지(초)
MAX_PARALLEL_FETCH = 4
UNCERTAIN_AFTER_DAYS = 7               # 오늘로부터 이만큼보다 먼 날은 예보가 바뀔 수 있다고 표시한다
PRUNE_AFTER = timedelta(days=30)

Key = tuple[int, int]                  # (lat10, lng10)
Days = list[dict]                      # openmeteo.parse_daily 의 반환 모양


def grid_key(lat: float, lng: float) -> Key:
    # round() 는 .5 를 짝수로 보내서 경계가 들쭉날쭉해지므로 floor(x + 0.5) 로 반올림한다.
    return (math.floor(lat * GRID + 0.5), math.floor(lng * GRID + 0.5))


def grid_center(key: Key) -> tuple[float, float]:
    return (key[0] / GRID, key[1] / GRID)


class WeatherStore(Protocol):
    async def load(self, keys: Sequence[Key]) -> dict[Key, tuple[datetime, Days]]: ...
    async def save(self, key: Key, days: Days, now: datetime) -> None: ...


class PgWeatherStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def load(self, keys: Sequence[Key]) -> dict[Key, tuple[datetime, Days]]:
        if not keys:
            return {}
        rows = await self._pool.fetch(
            """select c.lat10, c.lng10, c.fetched_at, c.days
                 from weather_cache c
                 join unnest($1::int[], $2::int[]) as k(lat10, lng10)
                   on c.lat10 = k.lat10 and c.lng10 = k.lng10""",
            [k[0] for k in keys], [k[1] for k in keys],
        )
        out: dict[Key, tuple[datetime, Days]] = {}
        for r in rows:
            days = r["days"]
            if isinstance(days, str):      # jsonb 코덱이 없으면 문자열로 온다
                days = json.loads(days)
            out[(r["lat10"], r["lng10"])] = (r["fetched_at"], days)
        return out

    async def save(self, key: Key, days: Days, now: datetime) -> None:
        await self._pool.execute(
            """insert into weather_cache (lat10, lng10, fetched_at, days)
               values ($1, $2, $3, $4::jsonb)
               on conflict (lat10, lng10) do update set fetched_at = excluded.fetched_at, days = excluded.days""",
            key[0], key[1], now, json.dumps(days),
        )

    async def prune(self, now: datetime | None = None) -> None:
        """오래 안 쓴 격자를 지운다. (여행이 끝나면 아무도 안 보는 격자가 남는다)"""
        cutoff = (now or datetime.now(timezone.utc)) - PRUNE_AFTER
        await self._pool.execute("delete from weather_cache where fetched_at < $1", cutoff)


Fetch = Callable[[float, float], Awaitable[Days]]


class ForecastService:
    def __init__(
        self,
        store: WeatherStore,
        fetch: Fetch = openmeteo.fetch_daily,
        *,
        ttl: timedelta = TTL,
        retry_after: float = RETRY_AFTER,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store, self._fetch, self._ttl, self._retry_after = store, fetch, ttl, retry_after
        self._clock, self._mono = clock, mono
        self._inflight: dict[Key, asyncio.Task[Days | None]] = {}
        self._failed_until: dict[Key, float] = {}
        self._sem = asyncio.Semaphore(MAX_PARALLEL_FETCH)

    async def forecasts(self, points: Iterable[tuple[float, float]]) -> dict[Key, Days | None]:
        """좌표들의 예보. 키는 격자(grid_key). 받을 수 없으면 그 격자는 None.
        호출한 쪽은 grid_key(lat, lng)로 자기 좌표의 결과를 찾는다."""
        keys = list(dict.fromkeys(grid_key(lat, lng) for lat, lng in points))
        cached = await self._store.load(keys)
        now = self._clock()

        result: dict[Key, Days | None] = {}
        to_refresh: list[Key] = []
        for k in keys:
            hit = cached.get(k)
            if hit and now - hit[0] < self._ttl:
                result[k] = hit[1]
            else:
                to_refresh.append(k)

        fetched = await asyncio.gather(*(self._refresh(k) for k in to_refresh))
        for k, days in zip(to_refresh, fetched):
            if days is not None:
                result[k] = days
            else:
                hit = cached.get(k)
                result[k] = hit[1] if hit else None     # 실패하면 낡은 예보라도
        return result

    async def _refresh(self, key: Key) -> Days | None:
        if self._failed_until.get(key, 0.0) > self._mono():
            return None
        task = self._inflight.get(key)
        if task is None:           # 같은 격자를 동시에 요청해도 외부 요청은 하나만
            task = asyncio.ensure_future(self._fetch_and_save(key))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _fetch_and_save(self, key: Key) -> Days | None:
        async with self._sem:
            try:
                days = await self._fetch(*grid_center(key))
            except ProviderError:
                self._failed_until[key] = self._mono() + self._retry_after
                return None
        if not days:               # 빈 응답은 실패로 본다 (저장하면 6시간 동안 빈 날씨가 된다)
            self._failed_until[key] = self._mono() + self._retry_after
            return None
        try:
            await self._store.save(key, days, self._clock())
        except Exception:          # 저장이 안 돼도 방금 받은 예보는 이번 응답에 쓴다
            pass
        return days


def day_weather(days: Days | None, day: date, today: date) -> dict | None:
    """그날의 예보를 UI(`DayWeather`)의 모양으로. 예보가 없거나 코드를 모르면 None (그 날은 숨긴다).
    도시 이름 관련 필드(placeName, placeSource, placeChangedFromPrev)는 호출한 쪽에서 덧붙인다."""
    if not days:
        return None
    iso = day.isoformat()
    entry = next((d for d in days if d["date"] == iso), None)
    if entry is None:
        return None
    cond = openmeteo.condition_for(entry["code"])
    if cond is None:
        return None
    return {
        "date": iso,
        "condition": cond[0],
        "label": cond[1],
        "tempMin": round(entry["tmin"]),
        "tempMax": round(entry["tmax"]),
        "precipProbability": entry["precip"],
        "uncertain": (day - today).days > UNCERTAIN_AFTER_DAYS,
    }
