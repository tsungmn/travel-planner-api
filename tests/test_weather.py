import asyncio
import functools
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.providers import ProviderError, openmeteo
from app.weather import (
    ForecastService, confidence_for, day_weather, grid_center, grid_key, rain_period,
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


def test_parse_daily_reads_feels_wind_rain_and_sun_and_tolerates_missing_fields():
    data = {"daily": {
        "time": ["2026-11-01", "2026-11-02"],
        "weather_code": [61, 3],
        "temperature_2m_max": [9.0, 8.0],
        "temperature_2m_min": [2.0, 1.0],
        "apparent_temperature_max": [6.5, None],
        "apparent_temperature_min": [-1.0, None],
        "wind_speed_10m_max": [9.4, None],
        "rain_sum": [3.0, None],
        "showers_sum": [1.5, None],
        "snowfall_sum": [0.0, None],
        "sunrise": ["2026-11-01T06:45", None],
        "sunset": ["2026-11-01T16:30", "bad"],
    }}
    a, b = openmeteo.parse_daily(data)
    assert (a["feels_min"], a["feels_max"], a["wind"], a["rain_mm"], a["snow_cm"]) == (-1.0, 6.5, 9.4, 4.5, 0.0)
    assert (a["sunrise"], a["sunset"]) == ("06:45", "16:30")
    # 값이 없거나 이상한 필드는 그 값만 None 이고 그날 예보는 남는다
    assert (b["feels_min"], b["wind"], b["rain_mm"], b["snow_cm"], b["sunrise"], b["sunset"]) == (None,) * 6
    assert b["code"] == 3


def hourly_rain(day, am, pm):
    """day 하루의 시간별 값: 오전 12시간에 am(mm)을, 오후 12시간에 pm(mm)을 고르게 나눈다."""
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    rain = [am / 12 if h < 12 else pm / 12 for h in range(24)]
    return {"time": times, "rain": rain, "showers": [0.0] * 24}


def test_parse_daily_splits_hourly_rain_into_morning_and_afternoon():
    daily = {"time": ["2026-11-01", "2026-11-02"], "weather_code": [61, 3],
             "temperature_2m_max": [9.0, 8.0], "temperature_2m_min": [2.0, 1.0]}
    h1, h2 = hourly_rain("2026-11-01", 1.2, 6.0), hourly_rain("2026-11-02", 0, 0)
    hourly = {"time": h1["time"] + h2["time"], "rain": h1["rain"] + h2["rain"], "showers": h1["showers"] + h2["showers"]}
    hourly["showers"][13] = 0.6                                    # 소나기도 합친다
    a, b = openmeteo.parse_daily({"daily": daily, "hourly": hourly})
    assert (a["rain_am_mm"], a["rain_pm_mm"]) == (1.2, 6.6)
    assert (b["rain_am_mm"], b["rain_pm_mm"]) == (0.0, 0.0)


def test_parse_daily_ignores_missing_or_broken_hourly_data():
    daily = {"time": ["2026-11-01"], "weather_code": [61], "temperature_2m_max": [9.0], "temperature_2m_min": [2.0]}
    for hourly in (None, {}, {"time": ["2026-11-01T00:00"], "rain": [1.0]}, {"time": ["x"], "rain": [1], "showers": [1]},
                   {"time": ["2026-11-01T00:00"], "rain": [1, 2], "showers": [1]}):
        data = {"daily": daily} if hourly is None else {"daily": daily, "hourly": hourly}
        out = openmeteo.parse_daily(data)                          # 예보 자체는 살아 있다
        assert out[0]["code"] == 61 and out[0]["rain_am_mm"] is None and out[0]["rain_pm_mm"] is None


@pytest.mark.parametrize("am,pm,expected", [
    (6.0, 0.0, "morning"), (0.0, 4.0, "afternoon"), (3.0, 3.0, "both"),
    (0.4, 5.0, "afternoon"),          # 오전은 0.5mm 미만이라 안 센다
    (0.9, 20.0, "afternoon"),         # 0.5mm 는 넘지만 그날 비의 25% 에 못 미친다 — "하루 종일"이 아니다
    (5.0, 5.0, "both"), (0.0, 0.0, None),
])
def test_rain_period(am, pm, expected):
    assert rain_period({"rain_am_mm": am, "rain_pm_mm": pm}) == expected


def test_rain_period_unknown_without_hourly_values():
    assert rain_period({}) is None and rain_period({"rain_am_mm": 3.0, "rain_pm_mm": None}) is None


def test_day_weather_reports_rain_period_only_when_rainy_and_not_far_off():
    today = date(2026, 11, 1)

    def at(offset, **kw):
        d = (today + timedelta(days=offset)).isoformat()
        return day_weather([{**days(d, code=61)[0], "rain_mm": 7.0, "rain_am_mm": 1.0, "rain_pm_mm": 6.0, **kw}],
                           today + timedelta(days=offset), today)

    assert at(0)["rainPeriod"] == "afternoon" and at(7)["rainPeriod"] == "afternoon"
    assert at(8)["rainy"] is True and at(8)["rainPeriod"] is None        # 먼 날은 시간대를 말하지 않는다
    assert at(0, rain_mm=0.2)["rainy"] is False and at(0, rain_mm=0.2)["rainPeriod"] is None
    assert at(0, rain_am_mm=None)["rainPeriod"] is None


def test_parse_daily_rejects_optional_series_of_the_wrong_length():
    base = {"time": ["2026-11-01"], "weather_code": [0], "temperature_2m_max": [5], "temperature_2m_min": [1]}
    with pytest.raises(ProviderError):
        openmeteo.parse_daily({"daily": {**base, "wind_speed_10m_max": [1, 2]}})


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
    assert q["wind_speed_unit"] == "ms"          # 웹의 강풍 기준(8)이 m/s 다
    for field in ("apparent_temperature_max", "wind_speed_10m_max", "rain_sum", "sunrise", "sunset"):
        assert field in q["daily"]
    assert q["hourly"] == "rain,showers"

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
    assert r[grid_key(*PRAGUE)].stale is False and r[grid_key(*PRAGUE)].fetched_at == NOW    # 방금 받았다
    await svc.forecasts([PRAGUE])
    assert len(calls) == 2         # 신선하니 다시 받지 않는다
    clock.now += timedelta(hours=5, minutes=59)
    cached = await svc.forecasts([PRAGUE])
    assert len(calls) == 2
    assert cached[grid_key(*PRAGUE)].fetched_at == NOW and cached[grid_key(*PRAGUE)].stale is False   # 저장된 시각 그대로
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
    assert r[key].days == store.rows[key][1] and n == 1  # 낡은 예보라도 쓴다
    assert r[key].stale is True and r[key].fetched_at == NOW - timedelta(hours=7)   # 그리고 낡았다고 알린다
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
def test_day_weather_forecast_shape_and_rounding():
    today = date(2026, 11, 1)
    w = day_weather(days("2026-11-01"), today, today)       # 새 필드가 없던 시절에 저장된 예보도 읽는다
    assert w == {
        "date": "2026-11-01", "source": "forecast", "confidence": "high", "condition": "cloudy",
        "tempMin": 1, "tempMax": 9, "feelsMin": None, "feelsMax": None, "windMax": None,
        "rainy": False, "rainPeriod": None, "precipMm": None, "precipProbability": 30,
        "sunrise": None, "sunset": None, "normal": None,
    }
    full = [{**days("2026-11-01", code=61)[0], "feels_min": -2.4, "feels_max": 5.5, "wind": 9.44,
             "rain_mm": 4.46, "sunrise": "06:45", "sunset": "16:30"}]
    w = day_weather(full, today, today)
    assert (w["feelsMin"], w["feelsMax"], w["windMax"]) == (-2, 6, 9.4)
    assert (w["rainy"], w["precipMm"], w["sunrise"], w["sunset"]) == (True, 4.5, "06:45", "16:30")


def test_day_weather_rainy_needs_a_millimeter_and_never_on_snow_days():
    today = date(2026, 11, 1)

    def rainy(code, rain_mm):
        return day_weather([{**days("2026-11-01", code=code)[0], "rain_mm": rain_mm}], today, today)

    assert rainy(61, 0.9)["rainy"] is False and rainy(61, 0.9)["precipMm"] is None
    assert rainy(61, 1.0)["rainy"] is True
    assert rainy(3, 2.0)["rainy"] is True                # 코드는 흐림이어도 비가 왔으면 비 온 날
    assert rainy(73, 5.0)["rainy"] is False              # 눈 오는 날은 비 온 날이 아니다
    # 강수량을 모르면 날씨 코드로 판단한다
    assert day_weather(days("2026-11-01", code=61), today, today)["rainy"] is True
    assert day_weather(days("2026-11-01", code=53), today, today)["rainy"] is False   # 이슬비
    assert day_weather(days("2026-11-01", code=0), today, today)["rainy"] is False


def test_confidence_by_distance_from_today():
    today = date(2026, 11, 1)
    got = [confidence_for(today + timedelta(days=n), today) for n in (-3, 0, 3, 4, 7, 8, 15)]
    assert got == ["high", "high", "high", "medium", "medium", "low", "low"]


def test_day_weather_hides_when_no_data():
    today = date(2026, 11, 1)
    assert day_weather(None, today, today) is None
    assert day_weather([], today, today) is None
    assert day_weather(days("2026-11-02"), today, today) is None             # 그 날짜가 없다
    assert day_weather(days("2026-11-01", code=1234), today, today) is None  # 모르는 코드
    past = day_weather(days("2026-10-30"), date(2026, 10, 30), today)
    assert past["confidence"] == "high"
