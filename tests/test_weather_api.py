from datetime import date, datetime, timedelta, timezone

import pytest

from app.weather import Forecast, grid_key
from app.weather_api import (
    build_day_weather, in_forecast_window, lookup_points, needed_days, normal_points, visible_days,
)
from app.weather_location import DayLocation

pytestmark = pytest.mark.no_db

TODAY = date(2026, 11, 1)
PRAGUE = (50.0755, 14.4378)
VIENNA = (48.2082, 16.3738)
FETCHED = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def loc(point, source="stops", name=None):
    return DayLocation(point[0], point[1], source, name)


def forecast(start, n, code=3, fetched=FETCHED, stale=False, **extra):
    days = [{"date": (start + timedelta(days=i)).isoformat(), "code": code, "tmin": 1.0, "tmax": 8.0, "precip": 20, **extra}
            for i in range(n)]
    return Forecast(days, fetched, stale)


def normals(rainy=0.3):
    """모든 날짜의 평년. 2026-11-01 ~ 2027-02-28 에 해당하는 MM-DD 를 채운다."""
    out, d = {}, date(2026, 1, 1)
    while d.year == 2026:
        out[d.strftime("%m-%d")] = {"tmin": 2.4, "tmax": 9.6, "cold": -3.0, "hot": 13.0, "rainy": rainy}
        d += timedelta(days=1)
    return out


def build(locations, names=None, start=TODAY, today=TODAY, extra=None, norm=None, forecasts=None):
    f = forecasts if forecasts is not None else {
        grid_key(*PRAGUE): forecast(start, 20), grid_key(*VIENNA): forecast(start, 20), **(extra or {})}
    n = norm if norm is not None else {grid_key(*PRAGUE): normals(), grid_key(*VIENNA): normals()}
    return build_day_weather(start_date=start, locations=locations, forecasts=f, normals=n, names=names or {}, today=today)


def days_of(result):
    return result["days"]


def test_window_is_past_3_days_to_next_15():
    assert in_forecast_window(TODAY - timedelta(days=3), TODAY)
    assert not in_forecast_window(TODAY - timedelta(days=4), TODAY)
    assert in_forecast_window(TODAY + timedelta(days=15), TODAY)
    assert not in_forecast_window(TODAY + timedelta(days=16), TODAY)
    assert needed_days(TODAY + timedelta(days=14), 5, TODAY) == [0, 1]
    assert needed_days(TODAY + timedelta(days=30), 5, TODAY) == []


def test_visible_days_drop_days_older_than_the_forecast_window():
    start = TODAY - timedelta(days=5)
    assert visible_days(start, 8, TODAY) == [2, 3, 4, 5, 6, 7]          # -3일부터
    assert visible_days(TODAY - timedelta(days=30), 5, TODAY) == []     # 지난 여행
    assert visible_days(TODAY + timedelta(days=60), 3, TODAY) == [0, 1, 2]


def test_lookup_points_adds_the_day_before_the_window_only():
    locs = [loc(PRAGUE), loc(VIENNA), None, loc(PRAGUE)]
    assert lookup_points(locs, [1, 2]) == [PRAGUE, VIENNA]           # 0일차(앞 일차) + 1일차, 2일차는 위치 없음
    assert lookup_points(locs, [0]) == [PRAGUE]
    assert lookup_points(locs, []) == []


# ----- 예보 / 평년 / 숨김 -----
def test_days_inside_the_window_use_the_forecast_and_the_rest_use_normals():
    start = TODAY + timedelta(days=14)                     # 1·2일차만 창 안(오늘+14, +15)
    out = build([loc(PRAGUE)] * 4, start=start, forecasts={grid_key(*PRAGUE): forecast(start, 2)})
    assert [(d["source"], d["confidence"]) for d in days_of(out)] == [
        ("forecast", "low"), ("forecast", "low"), ("normal", "low"), ("normal", "low")]
    assert [d["normal"] and d["normal"]["fallbackReason"] for d in days_of(out)] == [None, None, "out_of_range", "out_of_range"]
    assert days_of(out)[2]["tempMin"] == 2 and days_of(out)[2]["condition"] is None


def test_forecast_failure_inside_the_window_falls_back_to_normals_with_a_reason():
    out = build([loc(PRAGUE), loc(VIENNA)], forecasts={grid_key(*PRAGUE): forecast(TODAY, 20), grid_key(*VIENNA): None})
    assert [d["source"] for d in days_of(out)] == ["forecast", "normal"]
    assert days_of(out)[1]["normal"]["fallbackReason"] == "forecast_failed"
    # 예보 날짜가 빠진 경우도 같다
    out = build([loc(PRAGUE)], forecasts={grid_key(*PRAGUE): forecast(TODAY + timedelta(days=1), 5)})
    assert days_of(out)[0]["normal"]["fallbackReason"] == "forecast_failed"


def test_days_without_location_forecast_or_normals_are_left_out():
    names = {grid_key(*PRAGUE): "프라하"}
    out = build([loc(PRAGUE), None, loc(PRAGUE, "previous")], names)
    assert [w["date"] for w in days_of(out)] == ["2026-11-01", "2026-11-03"]
    assert days_of(out)[0]["placeName"] == "프라하" and days_of(out)[0]["condition"] == "cloudy"
    # 예보도 평년도 없으면 숨긴다
    assert days_of(build([loc(PRAGUE)], forecasts={grid_key(*PRAGUE): None}, norm={})) == []
    # 평년에 그 날짜가 없으면 그 날만 숨긴다
    partial = {grid_key(*PRAGUE): {"12-01": normals()["12-01"]}}
    far = build([loc(PRAGUE)] * 3, start=date(2026, 11, 30), forecasts={}, norm=partial)
    assert [w["date"] for w in days_of(far)] == ["2026-12-01"]


def test_past_days_never_fall_back_to_normals():
    start = TODAY - timedelta(days=6)                      # -6 ~ -1일. -3일부터가 창 안
    out = build([loc(PRAGUE)] * 6, start=start, forecasts={grid_key(*PRAGUE): forecast(start, 20)})
    assert [d["date"] for d in days_of(out)] == [(TODAY - timedelta(days=n)).isoformat() for n in (3, 2, 1)]
    assert all(d["source"] == "forecast" and d["confidence"] == "high" for d in days_of(out))
    # 창 안의 지난 날인데 예보가 없으면 평년 대신 숨긴다 (이미 지난 날의 "평년"은 의미가 없다)
    out = build([loc(PRAGUE)] * 6, start=start, forecasts={grid_key(*PRAGUE): None})
    assert days_of(out) == []


def test_a_fully_past_trip_is_empty():
    start = TODAY - timedelta(days=30)
    out = build([loc(PRAGUE)] * 3, start=start)
    assert out == {"days": [], "summary": None, "fetchedAt": None, "stale": False}


def test_normal_points_are_only_the_days_without_a_forecast():
    start = TODAY + timedelta(days=14)
    locs = [loc(PRAGUE), loc(PRAGUE), loc(VIENNA)]
    fc = {grid_key(*PRAGUE): forecast(start, 2), grid_key(*VIENNA): forecast(start, 2)}
    # 3일차(오늘+16)만 창 밖. 그 날의 위치(빈)와 이동일 표시에 쓸 앞 일차 위치(프라하)
    assert normal_points(start_date=start, locations=locs, forecasts=fc, today=TODAY) == [VIENNA, PRAGUE]
    # 가까운 여행은 평년이 필요 없다
    assert normal_points(start_date=TODAY, locations=locs, forecasts={grid_key(*PRAGUE): forecast(TODAY, 20),
                                                                     grid_key(*VIENNA): forecast(TODAY, 20)}, today=TODAY) == []
    # 예보를 못 받은 격자의 날은 필요하다. 지난 날과 위치 없는 날은 아니다
    pts = normal_points(start_date=TODAY - timedelta(days=1), locations=[loc(PRAGUE), None, loc(VIENNA)],
                        forecasts={}, today=TODAY)
    assert pts == [VIENNA]


# ----- 도시 이름 / 이동일 -----
def test_city_change_is_marked_only_when_names_differ():
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    out = days_of(build([loc(PRAGUE), loc(PRAGUE), loc(VIENNA), loc(VIENNA)], names))
    assert [w["placeChangedFromPrev"] for w in out] == [False, False, True, False]
    assert all(w["placeSource"] == "auto" for w in out)


def test_unknown_names_never_mark_a_change_and_weather_still_shows():
    out = days_of(build([loc(PRAGUE), loc(VIENNA)], names={grid_key(*PRAGUE): "프라하"}))      # 빈 이름 조회 실패
    assert [w["placeChangedFromPrev"] for w in out] == [False, False]
    assert out[1]["placeName"] is None and out[1]["condition"] == "cloudy"


def test_user_city_uses_its_own_name_and_flag_and_carries_to_next_day():
    locs = [loc(PRAGUE), loc(VIENNA, "user", "빈"), loc(VIENNA, "previous", "빈")]
    out = days_of(build(locs, names={grid_key(*PRAGUE): "프라하"}))
    assert [(w["placeName"], w["placeSource"], w["placeChangedFromPrev"]) for w in out] == [
        ("프라하", "auto", False), ("빈", "user", True), ("빈", "auto", False)]


def test_transit_day_carries_the_previous_city_forecast_for_the_same_date():
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    extra = {grid_key(*PRAGUE): forecast(TODAY, 20, code=61)}              # 프라하만 비
    out = days_of(build([loc(PRAGUE), loc(PRAGUE), loc(VIENNA), loc(VIENNA)], names, extra=extra))
    assert [w["transitFrom"] is not None for w in out] == [False, False, True, False]
    tf = out[2]["transitFrom"]
    assert (tf["placeName"], tf["condition"], tf["date"], tf["source"]) == ("프라하", "rain", "2026-11-03", "forecast")
    assert out[2]["condition"] == "cloudy" and tf["placeChangedFromPrev"] is False


def test_transit_from_can_be_a_normal_day_and_keeps_the_user_flag():
    names = {grid_key(*VIENNA): "빈"}
    start = TODAY + timedelta(days=30)
    out = days_of(build([loc(PRAGUE, "user", "프라하"), loc(VIENNA)], names, start=start, forecasts={}))
    assert out[1]["source"] == "normal"
    assert out[1]["transitFrom"]["source"] == "normal" and out[1]["transitFrom"]["placeSource"] == "user"


def test_transit_from_is_none_when_the_previous_city_has_no_weather_for_that_day():
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    out = days_of(build([loc(PRAGUE), loc(VIENNA)], names, extra={grid_key(*PRAGUE): forecast(TODAY, 1)},
                        norm={grid_key(*VIENNA): normals()}))       # 프라하는 첫날 예보뿐, 평년도 없다
    assert out[1]["placeChangedFromPrev"] is True and out[1]["transitFrom"] is None


def test_different_spelling_of_the_same_city_is_not_a_change():
    near = (PRAGUE[0] + 0.02, PRAGUE[1] + 0.02)                            # 2~3km 옆
    names = {grid_key(*PRAGUE): "Prague", grid_key(*near): "Praha"}
    out = days_of(build([loc(PRAGUE), loc(near)], names, extra={grid_key(*near): forecast(TODAY, 20)}))
    assert [w["placeChangedFromPrev"] for w in out] == [False, False]
    assert [w["transitFrom"] for w in out] == [None, None]


# ----- 요약 / 수신 시각 -----
def test_summary_of_a_forecast_only_trip():
    rainy = {"rain_mm": 3.0}
    fc = {grid_key(*PRAGUE): forecast(TODAY, 20, code=61, **rainy)}
    out = build([loc(PRAGUE)] * 3, forecasts=fc)
    assert out["summary"] == {
        "basis": "forecast", "tempMin": 1, "tempMax": 8, "rainyDays": 3, "snowDays": 0,
        "maxDiurnalRange": 7, "firstNormalDay": None, "lowConfidenceFromDay": None}
    snow = build([loc(PRAGUE)] * 2, forecasts={grid_key(*PRAGUE): forecast(TODAY, 20, code=73)})
    assert snow["summary"]["snowDays"] == 2 and snow["summary"]["rainyDays"] == 0


def test_summary_of_a_mixed_trip_counts_normal_rain_by_ratio_and_numbers_days_from_one():
    start = TODAY + timedelta(days=13)             # 오늘+13 ~ +15 는 창 안(3일), 그다음 3일은 창 밖
    fc = {grid_key(*PRAGUE): forecast(start, 3, code=61, rain_mm=3.0)}
    out = build([loc(PRAGUE)] * 6, start=start, forecasts=fc, norm={grid_key(*PRAGUE): normals(rainy=0.4)})
    s = out["summary"]
    assert s["basis"] == "mixed" and s["firstNormalDay"] == 4
    assert s["rainyDays"] == 3 + 1                  # 예보 3일 + 평년 3일 × 0.4 = 1.2 → 1
    assert s["lowConfidenceFromDay"] == 1           # 오늘+13 은 이미 low
    assert (s["tempMin"], s["tempMax"], s["maxDiurnalRange"]) == (1, 10, 8)    # 평년은 2~10, 예보는 1~8


def test_summary_of_a_normal_only_trip_and_low_confidence_start():
    start = TODAY + timedelta(days=40)
    out = build([loc(PRAGUE)] * 5, start=start, forecasts={}, norm={grid_key(*PRAGUE): normals(rainy=0.3)})
    s = out["summary"]
    assert (s["basis"], s["firstNormalDay"], s["lowConfidenceFromDay"], s["rainyDays"]) == ("normal", 1, None, 2)   # 1.5 → 2
    near = build([loc(PRAGUE)] * 12, forecasts={grid_key(*PRAGUE): forecast(TODAY, 20)})
    assert near["summary"]["lowConfidenceFromDay"] == 9      # 오늘+8 일째(9일차)부터 low


def test_summary_first_day_numbers_skip_hidden_days_but_keep_trip_day_numbers():
    start = TODAY + timedelta(days=30)
    out = build([None, loc(PRAGUE), loc(PRAGUE)], start=start, forecasts={}, norm={grid_key(*PRAGUE): normals()})
    assert out["summary"]["firstNormalDay"] == 2


def test_fetched_at_is_the_oldest_forecast_used_and_stale_if_any_is_stale():
    old = FETCHED - timedelta(hours=3)
    fc = {grid_key(*PRAGUE): forecast(TODAY, 20, fetched=FETCHED), grid_key(*VIENNA): forecast(TODAY, 20, fetched=old, stale=True)}
    names = {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"}
    out = build([loc(PRAGUE), loc(VIENNA)], names, forecasts=fc)
    assert out["fetchedAt"] == old and out["stale"] is True
    fresh = build([loc(PRAGUE)], forecasts={grid_key(*PRAGUE): forecast(TODAY, 20)})
    assert fresh["fetchedAt"] == FETCHED and fresh["stale"] is False


def test_no_forecast_used_means_no_fetched_at():
    start = TODAY + timedelta(days=40)
    out = build([loc(PRAGUE)] * 2, start=start, forecasts={})
    assert out["fetchedAt"] is None and out["stale"] is False
    # 예보를 받았지만 쓰지 않은 격자의 낡은 상태는 세지 않는다 (예보가 모두 평년으로 대체된 경우)
    out = build([loc(PRAGUE)], forecasts={grid_key(*PRAGUE): forecast(TODAY + timedelta(days=1), 3, stale=True)})
    assert out["days"][0]["source"] == "normal" and out["fetchedAt"] is None and out["stale"] is False
