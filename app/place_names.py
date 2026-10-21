"""날씨 격자의 도시 이름 조회와 캐시. 구조는 weather.ForecastService 와 같다.

- 이름은 격자(0.1°)당 한 번만 조회해 저장한다. 만료는 없다.
- 조회가 실패하면(또는 도시를 못 찾으면) 이름만 None 이고, 날씨는 그대로 보여 준다.
- 실패한 격자는 잠깐 다시 조회하지 않는다. (API 키가 없으면 아예 조회하지 않는다)
- 격자 중심 좌표로 조회하므로 누가 먼저 요청했는지와 상관없이 같은 격자는 같은 이름이다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Iterable, Protocol, Sequence

from .config import settings
from .providers import ProviderError, geoapify
from .weather import Key, grid_center, grid_key

RETRY_AFTER_ERROR = 60.0           # 요청이 실패한 격자를 다시 조회하기까지(초)
RETRY_AFTER_NOT_FOUND = 3600.0     # 도시가 없는 곳(바다 한가운데 등)은 오래 기다린다
MAX_PARALLEL = 4


class NameStore(Protocol):
    async def load(self, keys: Sequence[Key]) -> dict[Key, str]: ...
    async def save(self, key: Key, name: str) -> None: ...


class PgNameStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def load(self, keys: Sequence[Key]) -> dict[Key, str]:
        if not keys:
            return {}
        rows = await self._pool.fetch(
            """select n.lat10, n.lng10, n.name
                 from place_names n
                 join unnest($1::int[], $2::int[]) as k(lat10, lng10)
                   on n.lat10 = k.lat10 and n.lng10 = k.lng10""",
            [k[0] for k in keys], [k[1] for k in keys],
        )
        return {(r["lat10"], r["lng10"]): r["name"] for r in rows}

    async def save(self, key: Key, name: str) -> None:
        await self._pool.execute(
            """insert into place_names (lat10, lng10, name) values ($1, $2, $3)
               on conflict (lat10, lng10) do update set name = excluded.name""",
            key[0], key[1], name,
        )


Lookup = Callable[[float, float], Awaitable[str | None]]


class PlaceNameService:
    def __init__(
        self,
        store: NameStore,
        lookup: Lookup = geoapify.reverse_city,
        *,
        enabled: Callable[[], bool] = lambda: bool(settings.geoapify_api_key),
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store, self._lookup, self._enabled, self._mono = store, lookup, enabled, mono
        self._inflight: dict[Key, asyncio.Task[str | None]] = {}
        self._skip_until: dict[Key, float] = {}
        self._sem = asyncio.Semaphore(MAX_PARALLEL)

    async def names(self, points: Iterable[tuple[float, float]]) -> dict[Key, str | None]:
        """좌표들의 도시 이름. 키는 격자(grid_key). 알 수 없으면 None."""
        keys = list(dict.fromkeys(grid_key(lat, lng) for lat, lng in points))
        try:
            cached = await self._store.load(keys)
        except Exception:      # 이름은 부가 정보라서 저장소 문제로 날씨까지 막지 않는다
            cached = {}
        result: dict[Key, str | None] = {k: cached.get(k) for k in keys}
        missing = [k for k in keys if k not in cached]
        if missing and self._enabled():
            found = await asyncio.gather(*(self._resolve(k) for k in missing))
            result.update(zip(missing, found))
        return result

    async def _resolve(self, key: Key) -> str | None:
        if self._skip_until.get(key, 0.0) > self._mono():
            return None
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._lookup_and_save(key))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _lookup_and_save(self, key: Key) -> str | None:
        async with self._sem:
            try:
                name = await self._lookup(*grid_center(key))
            except ProviderError:
                self._skip_until[key] = self._mono() + RETRY_AFTER_ERROR
                return None
        if not name:
            self._skip_until[key] = self._mono() + RETRY_AFTER_NOT_FOUND
            return None
        try:
            await self._store.save(key, name)
        except Exception:
            pass
        return name
