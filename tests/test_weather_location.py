from datetime import date

import pytest

from app.weather_location import DayLocation, central_point, resolve_day_locations

pytestmark = pytest.mark.no_db

PRAGUE = (50.0755, 14.4378)
PRAGUE2 = (50.0875, 14.4213)
PRAGUE3 = (50.0911, 14.4010)
VIENNA = (48.2082, 16.3738)
ROME = (41.9028, 12.4964)
START = date(2026, 11, 1)


def stop(day, order, point):
    return {"day_index": day, "sort_order": order, "lat": point[0], "lng": point[1]}


def lodging(point, check_in, check_out):
    return {"lat": point[0], "lng": point[1], "check_in": check_in, "check_out": check_out}


def area(day, point):
    return {"day_index": day, "lat": point[0], "lng": point[1]}


def resolve(num_days=3, stops=(), areas=(), lodgings=(), destination=None, overrides=None):
    return resolve_day_locations(
        start_date=START, num_days=num_days, stops=list(stops), areas=list(areas),
        lodgings=list(lodgings), destination=destination, overrides=overrides,
    )


def at(loc, point):
    return (loc.lat, loc.lng) == point


# ----- 대표 위치 -----
def test_central_point_ignores_a_far_outlier_and_stays_inside_a_city():
    # 프라하 3곳 + 빈 1곳: 한가운데(두 도시 사이)가 아니라 프라하 안의 점이 나와야 한다
    assert central_point([PRAGUE, PRAGUE2, PRAGUE3, VIENNA]) in (PRAGUE, PRAGUE2, PRAGUE3)


def test_central_point_of_one_and_of_a_tie():
    assert central_point([ROME]) == ROME
    assert central_point([PRAGUE, VIENNA]) == PRAGUE    # 동점이면 앞선 점


# ----- 우선순위 -----
def test_stops_come_first_and_use_sort_order_not_list_order():
    stops = [stop(0, 2, VIENNA), stop(0, 0, PRAGUE)]    # 순서가 섞여 들어와도
    loc = resolve(1, stops=stops, destination=ROME)[0]
    assert loc.source == "stops" and at(loc, PRAGUE)    # 동점 → sort_order 가 앞선 PRAGUE


def test_user_city_beats_stops_and_keeps_its_name():
    loc = resolve(1, stops=[stop(0, 0, PRAGUE)], overrides={0: (*VIENNA, "빈")})[0]
    assert loc == DayLocation(*VIENNA, "user", "빈")


def test_area_is_used_when_there_are_no_stops_and_unassigned_areas_are_ignored():
    locs = resolve(2, areas=[area(0, ROME), area(None, VIENNA)])
    assert locs[0].source == "area" and at(locs[0], ROME)
    assert locs[1].source == "previous" and at(locs[1], ROME)   # 미지정 영역은 근거가 아님


def test_stops_beat_area_and_area_beats_lodging():
    h = lodging(VIENNA, date(2026, 11, 1), date(2026, 11, 4))
    assert resolve(1, stops=[stop(0, 0, PRAGUE)], areas=[area(0, ROME)], lodgings=[h])[0].source == "stops"
    assert resolve(1, areas=[area(0, ROME)], lodgings=[h])[0].source == "area"


# ----- 숙소 (그날 밤 기준) -----
def test_lodging_covers_nights_from_check_in_up_to_but_not_including_check_out():
    h = lodging(PRAGUE, date(2026, 11, 1), date(2026, 11, 3))   # 1일, 2일 밤
    locs = resolve(3, lodgings=[h])
    assert [l.source for l in locs] == ["lodging", "lodging", "previous"]
    assert at(locs[2], PRAGUE)                                   # 퇴실일은 전날 위치를 이어받음


def test_check_out_day_uses_the_new_hotel_when_moving_cities():
    a = lodging(PRAGUE, date(2026, 11, 1), date(2026, 11, 3))
    b = lodging(VIENNA, date(2026, 11, 3), date(2026, 11, 5))
    locs = resolve(4, lodgings=[a, b])
    assert [(l.source, (l.lat, l.lng)) for l in locs] == [
        ("lodging", PRAGUE), ("lodging", PRAGUE), ("lodging", VIENNA), ("lodging", VIENNA),
    ]


def test_lodging_without_check_out_only_counts_on_its_check_in_day():
    locs = resolve(2, lodgings=[lodging(ROME, date(2026, 11, 1), None)])
    assert locs[0].source == "lodging" and locs[1].source == "previous"


def test_lodging_without_dates_is_ignored():
    assert resolve(1, lodgings=[lodging(ROME, None, None)])[0] is None


# ----- 전날 / 목적지 / 없음 -----
def test_empty_days_inherit_the_previous_day_before_falling_back_to_the_destination():
    locs = resolve(4, stops=[stop(1, 0, VIENNA)], destination=PRAGUE)
    assert [l.source for l in locs] == ["destination", "stops", "previous", "previous"]
    assert at(locs[0], PRAGUE) and at(locs[2], VIENNA) and at(locs[3], VIENNA)


def test_user_city_name_is_inherited_by_following_empty_days():
    locs = resolve(3, overrides={0: (*ROME, "로마")})
    assert [(l.source, l.name) for l in locs] == [("user", "로마"), ("previous", "로마"), ("previous", "로마")]


def test_no_information_at_all_means_no_weather():
    assert resolve(3) == [None, None, None]


def test_length_matches_num_days_and_zero_days_is_empty():
    assert len(resolve(5, destination=ROME)) == 5
    assert resolve(0) == []


# ----- 동유럽 시나리오 -----
def test_prague_to_vienna_to_rome_trip():
    locs = resolve(
        6,
        stops=[stop(0, 0, PRAGUE), stop(0, 1, PRAGUE2), stop(1, 0, PRAGUE3),
               stop(3, 0, VIENNA), stop(4, 0, VIENNA), stop(5, 0, ROME)],
        destination=PRAGUE,
    )
    assert at(locs[0], PRAGUE) or at(locs[0], PRAGUE2)
    assert at(locs[1], PRAGUE3)
    assert at(locs[2], PRAGUE3)            # 장소 없는 날은 전날 도시
    assert at(locs[3], VIENNA) and at(locs[4], VIENNA) and at(locs[5], ROME)
