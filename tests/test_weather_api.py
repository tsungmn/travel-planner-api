from datetime import date, timedelta

import pytest

from app.weather import grid_key
from app.weather_api import build_day_weather, in_forecast_window, lookup_points, needed_days
from app.weather_location import DayLocation

pytestmark = pytest.mark.no_db

TODAY = date(2026, 11, 1)
PRAGUE = (50.0755, 14.4378)
VIENNA = (48.2082, 16.3738)


def loc(point, source="stops", name=None):
    return DayLocation(point[0], point[1], source, name)


def forecast(start, n, code=3):
    return [{"date": (start + timedelta(days=i)).isoformat(), "code": code, "tmin": 1.0, "tmax": 8.0, "precip": 20}
            for i in range(n)]


def build(locations, names=None, start=TODAY, today=TODAY, extra=None):
    f = {grid_key(*PRAGUE): forecast(start, 20), grid_key(*VIENNA): forecast(start, 20), **(extra or {})}
    return build_day_weather(start_date=start, locations=locations, forecasts=f, names=names or {}, today=today)


def test_window_is_past_3_days_to_next_15():
    assert in_forecast_window(TODAY - timedelta(days=3), TODAY)
    assert not in_forecast_window(TODAY - timedelta(days=4), TODAY)
    assert in_forecast_window(TODAY + timedelta(days=15), TODAY)
    assert not in_forecast_window(TODAY + timedelta(days=16), TODAY)
    assert needed_days(TODAY + timedelta(days=14), 5, TODAY) == [0, 1]
    assert needed_days(TODAY + timedelta(days=30), 5, TODAY) == []


def test_lookup_points_adds_the_day_before_the_window_only():
    locs = [loc(PRAGUE), loc(VIENNA), None, loc(PRAGUE)]
    assert lookup_points(locs, [1, 2]) == [PRAGUE, VIENNA]           # 0일차(앞 일차) + 1일차, 2일차는 위치 없음
    assert lookup_points(locs, [0]) == [PRAGUE]
    assert lookup_points(locs, []) == []


def test_days_without_location_or_forecast_are_left_out():
    names = {grid_key(*PRAGUE): "프라하"}
    out = build([loc(PRAGUE), None, loc(PRAGUE, "previous")], names)
    assert [w["date"] for w in out] == ["2026-11-01", "2026-11-03"]
    assert out[0]["placeName"] == "프라하" and out[0]["condition"] == "cloudy"
    far = build([loc(PRAGUE)] * 3, start=TODAY + timedelta(days=15))      # 3일차부터 16일 밖
    assert [w["date"] for w in far] == ["2026-11-16"]


def test_city_change_is_marked_only_when_names_differ():
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    out = build([loc(PRAGUE), loc(PRAGUE), loc(VIENNA), loc(VIENNA)], names)
    assert [w["placeChangedFromPrev"] for w in out] == [False, False, True, False]
    assert all(w["placeSource"] == "auto" for w in out)


def test_unknown_names_never_mark_a_change_and_weather_still_shows():
    out = build([loc(PRAGUE), loc(VIENNA)], names={grid_key(*PRAGUE): "프라하"})      # 빈 이름 조회 실패
    assert [w["placeChangedFromPrev"] for w in out] == [False, False]
    assert out[1]["placeName"] is None and out[1]["condition"] == "cloudy"


def test_user_city_uses_its_own_name_and_flag_and_carries_to_next_day():
    locs = [loc(PRAGUE), loc(VIENNA, "user", "빈"), loc(VIENNA, "previous", "빈")]
    out = build(locs, names={grid_key(*PRAGUE): "프라하"})
    assert [(w["placeName"], w["placeSource"], w["placeChangedFromPrev"]) for w in out] == [
        ("프라하", "auto", False), ("빈", "user", True), ("빈", "auto", False)]


def test_missing_forecast_for_a_grid_hides_only_those_days():
    out = build([loc(PRAGUE), loc(VIENNA)], extra={grid_key(*VIENNA): None})
    assert [w["date"] for w in out] == ["2026-11-01"]


def test_transit_day_carries_the_previous_city_forecast_for_the_same_date():
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    extra = {grid_key(*PRAGUE): forecast(TODAY, 20, code=61)}              # 프라하만 비
    out = build([loc(PRAGUE), loc(PRAGUE), loc(VIENNA), loc(VIENNA)], names, extra=extra)
    assert [w["transitFrom"] is not None for w in out] == [False, False, True, False]
    tf = out[2]["transitFrom"]
    assert (tf["placeName"], tf["condition"], tf["date"]) == ("프라하", "rain", "2026-11-03")
    assert out[2]["condition"] == "cloudy" and tf["placeChangedFromPrev"] is False


def test_transit_from_keeps_the_user_flag_of_the_previous_day():
    names = {grid_key(*VIENNA): "빈"}
    out = build([loc(PRAGUE, "user", "프라하"), loc(VIENNA)], names)
    assert out[1]["transitFrom"]["placeSource"] == "user"


def test_transit_from_is_none_when_the_previous_city_has_no_forecast_for_that_day():
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    out = build([loc(PRAGUE), loc(VIENNA)], names, extra={grid_key(*PRAGUE): forecast(TODAY, 1)})   # 첫날만 있다
    assert out[1]["placeChangedFromPrev"] is True and out[1]["transitFrom"] is None


def test_different_spelling_of_the_same_city_is_not_a_change():
    near = (PRAGUE[0] + 0.02, PRAGUE[1] + 0.02)                            # 2~3km 옆
    names = {grid_key(*PRAGUE): "Prague", grid_key(*near): "Praha"}
    out = build([loc(PRAGUE), loc(near)], names, extra={grid_key(*near): forecast(TODAY, 20)})
    assert [w["placeChangedFromPrev"] for w in out] == [False, False]
    assert [w["transitFrom"] for w in out] == [None, None]
