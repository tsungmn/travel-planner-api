import asyncio
import functools
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.providers import ProviderError, openmeteo
from app.weather import (
    ForecastService, day_weather, grid_center, grid_key,
)

pytestmark = pytest.mark.no_db


def aio(fn):
    """async 테스트를 asyncio.run 으로 돌린다. (pytest-asyncio 의존성을 늘리지 않으려고)"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
PRAGUE = (50.0755, 14.4378)
VIENNA = (48.2082, 16.3738)


def days(*dates, code=3):
    return [{"date": d, "code": code, "tmin": 1.4, "tmax": 8.6, "precip": 30} for d in dates]


# ----- 격자 -----
def test_grid_key_groups_nearby_points_and_rounds_half_up():
    assert grid_key(50.0755, 14.4378) == (501, 144)
    assert grid_key(50.0801, 14.4402) == (501, 144)          # 같은 격자
    assert grid_key(50.0755, 14.4378) != grid_key(48.2082, 16.3738)
    assert grid_key(50.05, 14.45) == (501, 145)              # .5 는 올림
    assert grid_key(-33.8688, 151.2093) == (-339, 1512)      # 남반구·음수
    assert grid_center((501, 144)) == pytest.approx((50.1, 14.4))


# ----- 응답 해석 -----
def test_parse_daily_skips_null_days_and_clamps_precip():
    data = {"daily": {
        "time": ["2026-11-01", "2026-11-02", "2026-11-03"],
        "weather_code": [3, None, 61],
        "temperature_2m_max": [9.0, 8.0, 7.0],
        "temperature_2m_min": [2.0, 1.0, 3.0],
        "precipitation_probability_max": [None, 20, 140],
    }}
    out = openmeteo.parse_daily(data)
    assert [d["date"] for d in out] == ["2026-11-01", "2026-11-03"]
    assert out[0]["precip"] is None and out[1]["precip"] == 100


def test_parse_daily_without_precip_field_and_bad_shapes():
    base = {"time": ["2026-11-01"], "weather_code": [0],
            "temperature_2m_max": [5], "temperature_2m_min": [1]}
    assert openmeteo.parse_daily({"daily": base})[0]["precip"] is None
    for bad in (None, {}, {"daily": {}}, {"daily": {**base, "time": ["x", "y"]}},
                {"daily": {**base, "time": "2026-11-01"}}):
        with pytest.raises(ProviderError):
            openmeteo.parse_daily(bad)
    # 날짜 형식이 이상한 날은 건너뛴다
    assert openmeteo.parse_daily({"daily": {**base, "time": ["내일"]}}) == []


@pytest.mark.parametrize("code,cond", [
    (0, "clear"), (1, "mostly-clear"), (2, "partly-cloudy"), (3, "cloudy"), (45, "fog"),
    (53, "drizzle"), (65, "rain"), (73, "snow"), (81, "showers"), (86, "snow"), (96, "thunderstorm"),
])
def test_condition_mapping(code, cond):
    assert openmeteo.condition_for(code)[0] == cond


def test_every_mapped_condition_exists_in_the_ui_type():
    ui = {"clear", "mostly-clear", "partly-cloudy", "cloudy", "fog", "drizzle", "rain",
          "showers", "snow", "thunderstorm"}
    assert {c for c, _ in openmeteo._WMO.values()} <= ui
    assert openmeteo.condition_for(1234) is None


@aio
async def test_fetch_daily_sends_expected_request_and_maps_errors(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["q"] = dict(req.url.params)
        return httpx.Response(200, json={"daily": {
            "time": ["2026-11-01"], "weather_code": [0],
            "temperature_2m_max": [5.0], "temperature_2m_min": [1.0],
            "precipitation_probability_max": [10]}})

    monkeypatch.setattr(openmeteo, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await openmeteo.fetch_daily(50.1, 14.4)
    assert out[0]["code"] == 0
    q = seen["q"]
    assert q["timezone"] == "auto" and q["forecast_days"] == "16" and q["past_days"] == "3"
    assert q["latitude"] == "50.1" and "weather_code" in q["daily"]

    for resp, reason in ((httpx.Response(429), "status_429"), (httpx.Response(200, text="<html>"), "bad_json")):
        monkeypatch.setattr(openmeteo, "client",
                            httpx.AsyncClient(transport=httpx.MockTransport(lambda r, resp=resp: resp)))
        with pytest.raises(ProviderError, match=reason):
            await openmeteo.fetch_daily(50.1, 14.4)

    def boom(req):
        raise httpx.ConnectError("x", request=req)
    monkeypatch.setattr(openmeteo, "client", httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(ProviderError, match="network"):
        await openmeteo.fetch_daily(50.1, 14.4)


# ----- 캐시 -----
class MemStore:
    def __init__(self):
        self.rows = {}
        self.saves = 0

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, days, now):
        self.rows[key] = (now, days)
        self.saves += 1


class Clock:
    def __init__(self):
        self.now, self.mono = NOW, 0.0


def make(store=None, fetch=None, **kw):
    clock = Clock()
    calls = []

    async def default_fetch(lat, lng):
        calls.append((lat, lng))
        return days("2026-11-01")

    svc = ForecastService(store or MemStore(), fetch or default_fetch,
                          clock=lambda: clock.now, mono=lambda: clock.mono, **kw)
    return svc, clock, calls


@aio
async def test_fetches_once_per_grid_then_serves_from_cache():
    svc, clock, calls = make()
    near = (50.0801, 14.4402)      # PRAGUE 와 같은 격자
    r = await svc.forecasts([PRAGUE, near, VIENNA])
    assert len(calls) == 2 and set(r) == {grid_key(*PRAGUE), grid_key(*VIENNA)}
    await svc.forecasts([PRAGUE])
    assert len(calls) == 2         # 신선하니 다시 받지 않는다
    clock.now += timedelta(hours=5, minutes=59)
    await svc.forecasts([PRAGUE])
    assert len(calls) == 2
    clock.now += timedelta(minutes=2)
    await svc.forecasts([PRAGUE])
    assert len(calls) == 3         # 6시간이 지나서 갱신


@aio
async def test_fetch_uses_grid_center_not_the_callers_point():
    svc, _, calls = make()
    await svc.forecasts([PRAGUE])
    assert calls == [grid_center(grid_key(*PRAGUE))]


@aio
async def test_failure_serves_stale_then_backs_off():
    store = MemStore()
    key = grid_key(*PRAGUE)
    store.rows[key] = (NOW - timedelta(hours=7), days("2026-11-01"))
    n = 0

    async def failing(lat, lng):
        nonlocal n
        n += 1
        raise ProviderError("network")

    svc, clock, _ = make(store, failing)
    r = await svc.forecasts([PRAGUE])
    assert r[key] == store.rows[key][1] and n == 1       # 낡은 예보라도 쓴다
    await svc.forecasts([PRAGUE])
    assert n == 1                                        # 잠깐은 다시 시도하지 않는다
    clock.mono += 61
    await svc.forecasts([PRAGUE])
    assert n == 2


@aio
async def test_failure_without_cache_is_none_and_empty_response_is_a_failure():
    async def failing(lat, lng):
        raise ProviderError("status_500")

    svc, _, _ = make(fetch=failing)
    assert (await svc.forecasts([PRAGUE]))[grid_key(*PRAGUE)] is None

    async def empty(lat, lng):
        return []

    store = MemStore()
    svc, _, _ = make(store, empty)
    assert (await svc.forecasts([PRAGUE]))[grid_key(*PRAGUE)] is None
    assert store.saves == 0                              # 빈 예보를 6시간 동안 저장하지 않는다


@aio
async def test_concurrent_requests_share_one_fetch():
    gate = asyncio.Event()
    n = 0

    async def slow(lat, lng):
        nonlocal n
        n += 1
        await gate.wait()
        return days("2026-11-01")

    svc, _, _ = make(fetch=slow)
    tasks = [asyncio.create_task(svc.forecasts([PRAGUE])) for _ in range(3)]
    await asyncio.sleep(0.01)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert n == 1 and all(r[grid_key(*PRAGUE)] for r in results)


@aio
async def test_store_failure_does_not_lose_the_fresh_forecast():
    class BrokenSave(MemStore):
        async def save(self, key, days, now):
            raise RuntimeError("db down")

    svc, _, _ = make(BrokenSave())
    assert (await svc.forecasts([PRAGUE]))[grid_key(*PRAGUE)] is not None


# ----- UI 모양 -----
def test_day_weather_shape_rounding_and_uncertain():
    today = date(2026, 11, 1)
    d = days("2026-11-01", "2026-11-08", "2026-11-09")
    w = day_weather(d, date(2026, 11, 1), today)
    assert w == {"date": "2026-11-01", "condition": "cloudy", "label": "흐림", "tempMin": 1, "tempMax": 9,
                 "precipProbability": 30, "uncertain": False}
    assert day_weather(d, date(2026, 11, 8), today)["uncertain"] is False    # 7일 뒤까지는 확실
    assert day_weather(d, date(2026, 11, 9), today)["uncertain"] is True


def test_day_weather_hides_when_no_data():
    today = date(2026, 11, 1)
    assert day_weather(None, today, today) is None
    assert day_weather([], today, today) is None
    assert day_weather(days("2026-11-02"), today, today) is None             # 그 날짜가 없다
    assert day_weather(days("2026-11-01", code=1234), today, today) is None  # 모르는 코드
    past = day_weather(days("2026-10-30"), date(2026, 10, 30), today)
    assert past["uncertain"] is False
