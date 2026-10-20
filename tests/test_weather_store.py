"""PgWeatherStore 가 실제 PostgreSQL 에서 저장·조회·정리를 하는지. (TEST_DATABASE_URL 필요)"""
import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg

from app.weather import PRUNE_AFTER, PgWeatherStore
from tests.conftest import TEST_URL

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
DAYS = [{"date": "2026-11-01", "code": 3, "tmin": 1.4, "tmax": 8.6, "precip": None}]


def with_store(fn):
    async def go():
        pool = await asyncpg.create_pool(TEST_URL, min_size=1, max_size=2)
        try:
            await pool.execute("truncate weather_cache")
            await fn(PgWeatherStore(pool), pool)
        finally:
            await pool.close()
    asyncio.run(go())


def test_save_load_roundtrip_and_upsert():
    async def body(store, _pool):
        assert await store.load([(501, 144)]) == {}
        assert await store.load([]) == {}
        await store.save((501, 144), DAYS, NOW)
        await store.save((-339, 1512), DAYS, NOW)       # 음수 좌표
        got = await store.load([(501, 144), (-339, 1512), (0, 0)])
        assert set(got) == {(501, 144), (-339, 1512)}
        assert got[(501, 144)] == (NOW, DAYS)           # null 도 그대로

        later = NOW + timedelta(hours=7)
        new_days = [{**DAYS[0], "code": 61}]
        await store.save((501, 144), new_days, later)   # 같은 격자는 덮어쓴다
        got = await store.load([(501, 144)])
        assert got[(501, 144)] == (later, new_days)
    with_store(body)


def test_prune_removes_only_old_rows():
    async def body(store, pool):
        await store.save((1, 1), DAYS, NOW - PRUNE_AFTER - timedelta(days=1))
        await store.save((2, 2), DAYS, NOW - timedelta(days=1))
        await store.prune(NOW)
        assert set(await store.load([(1, 1), (2, 2)])) == {(2, 2)}
    with_store(body)


def test_place_name_store_roundtrip():
    from app.place_names import PgNameStore

    async def go():
        pool = await asyncpg.create_pool(TEST_URL, min_size=1, max_size=2)
        try:
            await pool.execute("truncate place_names")
            store = PgNameStore(pool)
            assert await store.load([(501, 144)]) == {} and await store.load([]) == {}
            await store.save((501, 144), "프라하")
            await store.save((-339, 1512), "시드니")
            await store.save((501, 144), "Praha")      # 같은 격자는 덮어쓴다
            assert await store.load([(501, 144), (-339, 1512), (0, 0)]) == {(501, 144): "Praha", (-339, 1512): "시드니"}
        finally:
            await pool.close()
    asyncio.run(go())
