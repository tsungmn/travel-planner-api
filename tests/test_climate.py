import asyncio
import functools
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.climate import (
    NormalsService, compute_normals, history_range, normal_weather,
)
from app.providers import ProviderError, openmeteo
from app.weather import grid_center, grid_key

pytestmark = pytest.mark.no_db


def aio(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


TODAY = date(2026, 11, 1)
PRAGUE = (50.0755, 14.4378)


def history(start, end, *, tmin=2.0, tmax=9.0, rain=0.0):
    """start ~ end 매일 같은 값. tmin/tmax/rain 은 날짜(date)를 받는 함수여도 된다."""
    out, d = [], start
    while d <= end:
        f = lambda v: v(d) if callable(v) else v
        out.append({"date": d.isoformat(), "tmin": f(tmin), "tmax": f(tmax), "rain_mm": f(rain)})
        d += timedelta(days=1)
    return out


# ----- 기간 -----
def test_history_range_is_the_last_ten_complete_years():
    assert history_range(date(2026, 11, 1)) == (date(2016, 1, 1), date(2025, 12, 31))
    assert history_range(date(2027, 1, 1)) == (date(2017, 1, 1), date(2026, 12, 31))


# ----- 계산 -----
def test_compute_normals_mean_percentiles_and_rainy_ratio():
    # 2016~2025 매일 tmin 은 날짜의 일(day)과 같고 tmax 는 그 +10. 비는 3일마다 하루(2mm), 나머지는 0.5mm
    h = history(date(2016, 1, 1), date(2025, 12, 31),
                tmin=lambda d: float(d.day), tmax=lambda d: float(d.day) + 10,
                rain=lambda d: 2.0 if d.toordinal() % 3 == 0 else 0.5)
    n = compute_normals(h)["06-15"]
    # 6/12 ~ 6/18 의 일(day)은 12~18 → 평균 15. 열 해 동안 같다.
    assert n["tmin"] == 15.0 and n["tmax"] == 25.0
    assert n["cold"] == 12.0 and n["hot"] == 28.0         # 하위·상위 10%: 표본 70개 중 가장 낮은/높은 7개 값
    assert n["rainy"] == pytest.approx(1 / 3, abs=0.05)   # 1mm 이상은 3일에 하루


def test_percentile_interpolates_linearly():
    from app.climate import _percentile
    assert _percentile([1, 2, 3, 4, 5], 10) == pytest.approx(1.4)
    assert _percentile([1, 2, 3, 4, 5], 90) == pytest.approx(4.6)
    assert _percentile([7], 10) == 7 and _percentile([1, 2], 100) == 2


def test_compute_normals_covers_every_day_including_feb_29():
    out = compute_normals(history(date(2016, 1, 1), date(2025, 12, 31)))
    assert len(out) == 366 and {"01-01", "02-29", "12-31"} <= set(out)


def test_compute_normals_skips_dates_with_too_few_samples():
    h = history(date(2025, 11, 1), date(2025, 11, 30))     # 한 해의 11월뿐
    out = compute_normals(h)
    assert "11-15" not in out                              # 표본 7개
    assert compute_normals([]) == {}


def test_compute_normals_ignores_missing_rain_but_keeps_temperatures():
    h = history(date(2016, 1, 1), date(2025, 12, 31), rain=None)
    assert compute_normals(h) == {}                        # 비 표본이 없으면 평년을 만들지 않는다


def test_compute_normals_rounds_to_one_and_two_decimals():
    h = history(date(2016, 1, 1), date(2025, 12, 31), tmin=1.23456, tmax=8.76543,
                rain=lambda d: 2.0 if d.year % 3 == 0 else 0.0)
    n = compute_normals(h)["05-05"]
    assert n["tmin"] == 1.2 and n["tmax"] == 8.8
    assert n["rainy"] == round(n["rainy"], 2)


# ----- UI 모양 -----
def test_normal_weather_shape_and_rain_likely_threshold():
    normals = {"11-03": {"tmin": 1.5, "tmax": 8.4, "cold": -3.2, "hot": 12.6, "rainy": 0.4},
               "11-04": {"tmin": 1.5, "tmax": 8.4, "cold": -3.2, "hot": 12.6, "rainy": 0.39}}
    w = normal_weather(normals, date(2026, 11, 3), "out_of_range")
    assert w == {
        "date": "2026-11-03", "source": "normal", "confidence": "low", "condition": None,
        "tempMin": 2, "tempMax": 8, "feelsMin": None, "feelsMax": None, "windMax": None,
        "rainy": None, "rainPeriod": None, "precipMm": None, "precipProbability": None,
        "sunrise": None, "sunset": None,
        "normal": {"coldTemp": -3, "hotTemp": 13, "rainyRatio": 0.4, "rainLikely": True,
                   "fallbackReason": "out_of_range"},
    }
    assert normal_weather(normals, date(2026, 11, 4), "forecast_failed")["normal"]["rainLikely"] is False
    assert normal_weather(normals, date(2026, 11, 4), "forecast_failed")["normal"]["fallbackReason"] == "forecast_failed"
    assert normal_weather(normals, date(2026, 11, 5), "out_of_range") is None     # 그 날짜의 평년이 없다
    assert normal_weather(None, date(2026, 11, 3), "out_of_range") is None


# ----- 응답 해석·요청 -----
def test_parse_history_skips_null_temperature_days_and_keeps_null_rain():
    data = {"daily": {"time": ["2025-11-01", "2025-11-02", "2025-11-03", "x"],
                      "temperature_2m_max": [9.0, None, 7.0, 1.0],
                      "temperature_2m_min": [2.0, 1.0, 3.0, 1.0],
                      "rain_sum": [0.0, 1.0, None, 0.0]}}
    out = openmeteo.parse_history(data)
    assert [(d["date"], d["rain_mm"]) for d in out] == [("2025-11-01", 0.0), ("2025-11-03", None)]
    for bad in (None, {}, {"daily": {}}, {"daily": {**data["daily"], "rain_sum": [1]}}):
        with pytest.raises(ProviderError):
            openmeteo.parse_history(bad)


@aio
async def test_fetch_history_sends_expected_request_and_maps_errors(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["q"] = str(req.url), dict(req.url.params)
        return httpx.Response(200, json={"daily": {
            "time": ["2016-01-01"], "temperature_2m_max": [5.0], "temperature_2m_min": [1.0], "rain_sum": [0.0]}})

    monkeypatch.setattr(openmeteo, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await openmeteo.fetch_history(50.1, 14.4, date(2016, 1, 1), date(2025, 12, 31))
    assert out[0]["tmax"] == 5.0
    assert seen["url"].startswith("https://archive-api.open-meteo.com/v1/archive")
    q = seen["q"]
    assert (q["start_date"], q["end_date"], q["timezone"]) == ("2016-01-01", "2025-12-31", "auto")
    assert q["daily"] == "temperature_2m_max,temperature_2m_min,rain_sum"

    monkeypatch.setattr(openmeteo, "client", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))))
    with pytest.raises(ProviderError, match="status_429"):
        await openmeteo.fetch_history(50.1, 14.4, date(2016, 1, 1), date(2025, 12, 31))


# ----- 캐시 -----
NORMALS = {"11-01": {"tmin": 1.0, "tmax": 8.0, "cold": -2.0, "hot": 12.0, "rainy": 0.3}}


class MemStore:
    def __init__(self):
        self.rows, self.saves = {}, 0

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, through_year, normals, now):
        self.rows[key] = (through_year, normals)
        self.saves += 1


class Clock:
    def __init__(self):
        self.today, self.mono = TODAY, 0.0


def make(store=None, fetch=None):
    clock, calls = Clock(), []

    async def default_fetch(lat, lng, start, end):
        calls.append((lat, lng, start, end))
        return history(start, end)

    svc = NormalsService(store or MemStore(), fetch or default_fetch,
                         today=lambda: clock.today, mono=lambda: clock.mono,
                         clock=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
    return svc, clock, calls


@aio
async def test_fetches_ten_years_once_per_grid_then_serves_from_cache_until_new_year():
    svc, clock, calls = make()
    near = (50.0801, 14.4402)                                  # PRAGUE 와 같은 격자
    r = await svc.normals([PRAGUE, near])
    key = grid_key(*PRAGUE)
    assert len(calls) == 1 and len(r[key]) == 366
    assert calls[0] == (*grid_center(key), date(2016, 1, 1), date(2025, 12, 31))
    await svc.normals([PRAGUE])
    assert len(calls) == 1                                     # 올해 안에는 다시 받지 않는다
    clock.today = date(2027, 1, 2)
    await svc.normals([PRAGUE])
    assert len(calls) == 2 and calls[1][2:] == (date(2017, 1, 1), date(2026, 12, 31))   # 새해에는 한 해 밀어서


@aio
async def test_failure_serves_last_years_normals_then_backs_off():
    store = MemStore()
    key = grid_key(*PRAGUE)
    store.rows[key] = (2024, NORMALS)                          # 재작년까지 계산한 평년
    n = 0

    async def failing(lat, lng, start, end):
        nonlocal n
        n += 1
        raise ProviderError("network")

    svc, clock, _ = make(store, failing)
    assert (await svc.normals([PRAGUE]))[key] == NORMALS and n == 1
    await svc.normals([PRAGUE])
    assert n == 1                                              # 잠깐은 다시 시도하지 않는다
    clock.mono += 301
    await svc.normals([PRAGUE])
    assert n == 2


@aio
async def test_failure_without_cache_is_none_and_thin_history_is_a_failure():
    async def failing(lat, lng, start, end):
        raise ProviderError("status_500")

    svc, _, _ = make(fetch=failing)
    assert (await svc.normals([PRAGUE]))[grid_key(*PRAGUE)] is None

    async def thin(lat, lng, start, end):
        return history(date(2025, 11, 1), date(2025, 11, 5))

    store = MemStore()
    svc, _, _ = make(store, thin)
    assert (await svc.normals([PRAGUE]))[grid_key(*PRAGUE)] is None
    assert store.saves == 0                                    # 빈 평년을 1년 동안 저장하지 않는다


@aio
async def test_concurrent_requests_share_one_fetch_and_store_failure_keeps_the_result():
    gate, n = asyncio.Event(), 0

    async def slow(lat, lng, start, end):
        nonlocal n
        n += 1
        await gate.wait()
        return history(start, end)

    svc, _, _ = make(fetch=slow)
    tasks = [asyncio.create_task(svc.normals([PRAGUE])) for _ in range(3)]
    await asyncio.sleep(0.01)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert n == 1 and all(r[grid_key(*PRAGUE)] for r in results)

    class BrokenSave(MemStore):
        async def save(self, *a):
            raise RuntimeError("db down")

    svc, _, _ = make(BrokenSave())
    assert (await svc.normals([PRAGUE]))[grid_key(*PRAGUE)] is not None
