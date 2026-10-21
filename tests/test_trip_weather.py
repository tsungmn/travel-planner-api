"""여행 하나의 날씨 흐름(compose_trip_weather)을 시나리오별로. DB·네트워크 없이, 실제 ForecastService 와
NormalsService 에 가짜 외부 호출을 끼워서 \"무엇을 몇 번 요청하는지\"까지 확인한다."""
import asyncio
import functools
from datetime import date, datetime, timedelta, timezone

import pytest

from app.climate import NormalsService
from app.providers import ProviderError
from app.trip_weather import compose_trip_weather
from app.weather import ForecastService
from app.weather_location import DayLocation

pytestmark = pytest.mark.no_db

TODAY = date(2026, 11, 1)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
PRAGUE = (50.0755, 14.4378)
VIENNA = (48.2082, 16.3738)


def aio(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


class MemForecastStore:
    def __init__(self):
        self.rows = {}

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, days, now):
        self.rows[key] = (now, days)


class MemClimateStore:
    def __init__(self):
        self.rows = {}

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, through_year, normals, now):
        self.rows[key] = (through_year, normals)


class FakeNames:
    def __init__(self, table=None, fail=False):
        self.table, self.fail, self.calls = table or {}, fail, []

    async def names(self, points):
        self.calls.append(list(points))
        from app.weather import grid_key
        return {grid_key(*p): (None if self.fail else self.table.get(grid_key(*p))) for p in points}


class World:
    """외부 서버를 흉내 낸다. down_* 를 켜면 그 서버는 ProviderError 를 낸다."""

    def __init__(self, *, down_forecast=False, down_history=False):
        self.down_forecast, self.down_history = down_forecast, down_history
        self.forecast_calls, self.history_calls = [], []
        self.forecast = ForecastService(MemForecastStore(), self._forecast, clock=lambda: NOW)
        self.normals = NormalsService(MemClimateStore(), self._history, today=lambda: TODAY, clock=lambda: NOW)

    async def _forecast(self, lat, lng):
        self.forecast_calls.append((lat, lng))
        if self.down_forecast:
            raise ProviderError("network")
        return [{"date": (TODAY + timedelta(days=i)).isoformat(), "code": 3, "tmin": 1.0, "tmax": 8.0, "precip": 20}
                for i in range(-3, 16)]

    async def _history(self, lat, lng, start, end):
        self.history_calls.append((lat, lng, start, end))
        if self.down_history:
            raise ProviderError("network")
        out, d = [], start
        while d <= end:
            out.append({"date": d.isoformat(), "tmin": 2.0, "tmax": 9.0, "rain_mm": 2.0 if d.year % 2 == 0 else 0.0})
            d += timedelta(days=1)
        return out

    async def run(self, locations, start, names=None):
        return await compose_trip_weather(
            start_date=start, locations=locations, forecast=self.forecast, normals=self.normals,
            names=names or FakeNames(), today=TODAY)

    def external_calls(self):
        return len(self.forecast_calls) + len(self.history_calls)


def where(point, n, source="stops"):
    return [DayLocation(point[0], point[1], source)] * n


@aio
async def test_near_trip_uses_only_the_forecast():
    w = World()
    out = await w.run(where(PRAGUE, 3), TODAY)
    assert [d["source"] for d in out["days"]] == ["forecast"] * 3
    assert len(w.forecast_calls) == 1 and w.history_calls == []        # 평년은 건드리지 않는다
    assert out["summary"]["basis"] == "forecast" and out["fetchedAt"] == NOW and out["stale"] is False


@aio
async def test_far_trip_uses_only_normals_and_never_asks_for_a_forecast():
    w = World()
    start = TODAY + timedelta(days=40)
    out = await w.run(where(PRAGUE, 3), start)
    assert [d["source"] for d in out["days"]] == ["normal"] * 3
    assert w.forecast_calls == [] and len(w.history_calls) == 1
    assert w.history_calls[0][2:] == (date(2016, 1, 1), date(2025, 12, 31))   # 완결된 10년
    assert out["summary"]["basis"] == "normal" and out["fetchedAt"] is None
    await w.run(where(PRAGUE, 3), start)
    assert len(w.history_calls) == 1                                   # 같은 격자의 평년은 다시 받지 않는다


@aio
async def test_trip_across_the_forecast_edge_is_mixed_with_one_request_each():
    w = World()
    start = TODAY + timedelta(days=14)                                 # +14, +15 는 창 안
    out = await w.run(where(PRAGUE, 5), start)
    assert [d["source"] for d in out["days"]] == ["forecast", "forecast", "normal", "normal", "normal"]
    assert out["summary"]["basis"] == "mixed" and out["summary"]["firstNormalDay"] == 3
    assert len(w.forecast_calls) == 1 and len(w.history_calls) == 1


@aio
async def test_trip_that_changes_city_at_the_edge_fetches_each_grid_once():
    w = World()
    locs = where(PRAGUE, 2) + where(VIENNA, 2)
    out = await w.run(locs, TODAY + timedelta(days=14), FakeNames({(501, 144): "프라하", (482, 164): "빈"}))
    assert [d["source"] for d in out["days"]] == ["forecast", "forecast", "normal", "normal"]
    assert len(w.forecast_calls) == 1                                  # 프라하만 (빈의 날은 모두 창 밖)
    assert len(w.history_calls) == 2                                   # 프라하(이동일 표시용)와 빈
    assert out["days"][2]["placeChangedFromPrev"] is True and out["days"][2]["transitFrom"]["source"] == "normal"


@aio
async def test_no_location_means_no_weather_and_no_requests():
    w, names = World(), FakeNames()
    out = await w.run([None, None, None], TODAY, names)
    assert out == {"days": [], "summary": None, "fetchedAt": None, "stale": False}
    assert w.external_calls() == 0 and names.calls == [[]]


@aio
async def test_past_trip_means_no_weather_and_no_requests():
    w, names = World(), FakeNames()
    out = await w.run(where(PRAGUE, 3), TODAY - timedelta(days=30), names)
    assert out["days"] == [] and out["summary"] is None
    assert w.external_calls() == 0 and names.calls == [[]]


@aio
async def test_trip_in_progress_still_shows_the_days_just_passed():
    w = World()
    out = await w.run(where(PRAGUE, 6), TODAY - timedelta(days=5))     # -5 ~ 0일. -3일부터 보인다
    assert [d["date"] for d in out["days"]] == [(TODAY + timedelta(days=n)).isoformat() for n in (-3, -2, -1, 0)]
    assert {d["source"] for d in out["days"]} == {"forecast"}


@aio
async def test_forecast_server_blocked_falls_back_to_normals_for_the_whole_trip():
    w = World(down_forecast=True)
    out = await w.run(where(PRAGUE, 3), TODAY)
    assert [d["source"] for d in out["days"]] == ["normal"] * 3
    assert {d["normal"]["fallbackReason"] for d in out["days"]} == {"forecast_failed"}
    assert out["fetchedAt"] is None and out["stale"] is False


@aio
async def test_everything_blocked_gives_an_empty_answer_not_an_error():
    w = World(down_forecast=True, down_history=True)
    for start in (TODAY, TODAY + timedelta(days=40)):
        out = await w.run(where(PRAGUE, 3), start)
        assert out == {"days": [], "summary": None, "fetchedAt": None, "stale": False}


@aio
async def test_normals_server_blocked_keeps_the_forecast_days_of_a_mixed_trip():
    w = World(down_history=True)
    out = await w.run(where(PRAGUE, 5), TODAY + timedelta(days=14))
    assert [d["source"] for d in out["days"]] == ["forecast", "forecast"]
    assert out["summary"]["basis"] == "forecast" and out["summary"]["firstNormalDay"] is None


@aio
async def test_old_forecast_is_reported_stale_when_the_refresh_is_blocked():
    w = World()
    await w.run(where(PRAGUE, 3), TODAY)
    w.forecast._clock = lambda: NOW + timedelta(hours=7)               # TTL(6시간)이 지났다
    w.down_forecast = True
    out = await w.run(where(PRAGUE, 3), TODAY)
    assert [d["source"] for d in out["days"]] == ["forecast"] * 3
    assert out["stale"] is True and out["fetchedAt"] == NOW


@aio
async def test_name_lookup_failure_never_blocks_the_weather():
    w = World()
    out = await w.run(where(PRAGUE, 3), TODAY, FakeNames(fail=True))
    assert len(out["days"]) == 3 and all(d["placeName"] is None for d in out["days"])


@aio
async def test_user_named_days_are_not_looked_up():
    w, names = World(), FakeNames()
    locs = [DayLocation(*VIENNA, "user", "빈"), DayLocation(*PRAGUE, "stops")]
    out = await w.run(locs, TODAY, names)
    assert names.calls == [[PRAGUE]] and out["days"][0]["placeName"] == "빈"
