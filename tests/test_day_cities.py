"""사용자가 정하는 일차별 날씨 도시. 도시 검색 응답 해석(순수 함수)과 저장·날씨 반영(통합)을 나눠 시험한다."""
from datetime import datetime, timedelta, timezone

import pytest

from app.providers import ProviderError, geoapify, openmeteo
from app.routers import day_cities
from tests.conftest import _run
from tests.test_weather_endpoints import TODAY, add_stop, ext, mk_trip   # noqa: F401  (ext 는 fixture)


# ---------- 도시 검색 응답 해석: DB 없이 ----------
class TestParseCities:
    pytestmark = pytest.mark.no_db

    def test_reads_fields_and_skips_broken_entries(self):
        data = {"results": [
            {"id": 2761369, "name": "빈", "latitude": 48.2085, "longitude": 16.3721, "country": "오스트리아", "admin1": "빈"},
            {"id": 1, "name": "", "latitude": 1, "longitude": 1},                   # 이름 없음
            {"id": 2, "name": "좌표없음"},                                          # 좌표 없음
            {"id": 3, "name": "범위밖", "latitude": 91, "longitude": 0},            # 위도 범위 밖
            "문자열",                                                               # 항목 모양이 아님
            {"name": "프라하", "latitude": 50.088, "longitude": 14.42},            # id/country 없음
        ]}
        out = openmeteo.parse_cities(data)
        assert [c["name"] for c in out] == ["빈", "프라하"]
        assert out[0] == {"id": "2761369", "name": "빈", "country": "오스트리아", "admin1": "빈", "lat": 48.2085, "lng": 16.3721}
        assert out[1]["id"] == "50.0880,14.4200" and out[1]["country"] is None and out[1]["admin1"] is None

    def test_no_results_key_means_empty(self):
        assert openmeteo.parse_cities({"generationtime_ms": 0.3}) == []

    def test_limit(self):
        data = {"results": [{"name": f"c{i}", "latitude": i, "longitude": i} for i in range(20)]}
        assert len(openmeteo.parse_cities(data, limit=5)) == 5

    @pytest.mark.parametrize("bad", [None, [], "x", {"results": "x"}])
    def test_bad_shape(self, bad):
        with pytest.raises(ProviderError):
            openmeteo.parse_cities(bad)


# ---------- 통합 ----------
VIENNA = {"name": "빈", "lat": 48.2082, "lng": 16.3738}


def put(client, h, t, from_day, to_day, city=VIENNA):
    return client.put(f"/api/trips/{t['id']}/day-cities", json={"from_day": from_day, "to_day": to_day, **city}, headers=h)


def names(client, h, t):
    r = client.get(f"/api/trips/{t['id']}/weather", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["days"]


@pytest.fixture(autouse=True)
def _fresh_limiter():
    day_cities._search_limit._hits.clear()


def test_user_city_beats_stops_and_marks_source(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)                                     # 목적지는 프라하
    add_stop(client, a, t, 1, 50.0755, 14.4378)               # 2일차에 프라하 장소가 있어도
    assert put(client, a, t, 1, 1).status_code == 204         # 사용자가 2일차를 빈으로 정하면 빈이다
    d = names(client, a, t)
    assert [x["placeName"] for x in d] == ["프라하", "빈", "빈"]          # 3일차는 장소가 없어 전날(빈)을 이어받는다
    assert [x["placeSource"] for x in d] == ["auto", "user", "auto"]
    assert [x["placeChangedFromPrev"] for x in d] == [False, True, False]
    assert [x["condition"] for x in d] == ["rain", "clear", "clear"]      # 예보도 빈 좌표로 받았다


def test_range_is_spread_over_each_day_and_can_be_replaced(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    assert put(client, a, t, 1, 2).status_code == 204         # "끝까지"
    d = names(client, a, t)
    assert [x["placeName"] for x in d] == ["프라하", "빈", "빈"]
    assert [x["placeSource"] for x in d] == ["auto", "user", "user"]
    assert [x["placeChangedFromPrev"] for x in d] == [False, True, False]

    rome = {"name": "로마", "lat": 41.9028, "lng": 12.4964}
    assert put(client, a, t, 2, 2, rome).status_code == 204   # 같은 일차를 다시 정하면 바뀐다
    assert put(client, a, t, 2, 2, rome).status_code == 204   # 같은 요청을 또 보내도 같다
    assert [x["placeName"] for x in names(client, a, t)] == ["프라하", "빈", "로마"]
    n = _fetch_count(t["id"])
    assert n == 2


def test_clear_goes_back_to_auto(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    put(client, a, t, 0, 2)
    r = client.delete(f"/api/trips/{t['id']}/day-cities?from_day=1&to_day=5", headers=a)   # 지정 없는 일차가 섞여도 된다
    assert r.status_code == 204
    d = names(client, a, t)
    assert [x["placeSource"] for x in d] == ["user", "auto", "auto"]
    # 되돌린 일차는 전날(빈)을 이어받는다. 이름은 이어받지만 사용자가 정한 날은 아니다
    assert [x["placeName"] for x in d] == ["빈", "빈", "빈"]
    client.delete(f"/api/trips/{t['id']}/day-cities?from_day=0&to_day=0", headers=a)
    assert [x["placeName"] for x in names(client, a, t)] == ["프라하"] * 3
    assert client.delete(f"/api/trips/{t['id']}/day-cities?from_day=2&to_day=1", headers=a).status_code == 422


def test_user_named_city_needs_no_name_lookup(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    put(client, a, t, 0, 2)
    assert len(names(client, a, t)) == 3
    assert ext["city"] == []                                  # 이름은 저장된 것을 쓴다. 역지오코딩 요청이 없다
    assert ext["forecast"] and all(lat < 49 for lat, _ in ext["forecast"])


def test_validation(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)                                     # 3일: 0 ~ 2
    r = put(client, a, t, 0, 3)
    assert r.status_code == 422 and r.json()["detail"] == "day_out_of_range"
    for body in (
        {"from_day": 2, "to_day": 1, **VIENNA},
        {"from_day": -1, "to_day": 1, **VIENNA},
        {"from_day": 0, "to_day": 0, "name": "   ", "lat": 1, "lng": 1},
        {"from_day": 0, "to_day": 0, "name": "x" * 101, "lat": 1, "lng": 1},
        {"from_day": 0, "to_day": 0, "name": "빈", "lat": 91, "lng": 0},
        {"from_day": 0, "to_day": 0, "name": "빈", "lat": 0, "lng": 181},
    ):
        r = client.put(f"/api/trips/{t['id']}/day-cities", json=body, headers=a)
        assert r.status_code == 422 and r.json()["detail"] == "validation_error", body
    assert _fetch_count(t["id"]) == 0                         # 실패한 요청은 아무것도 저장하지 않는다


def test_name_is_trimmed(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    put(client, a, t, 0, 0, {**VIENNA, "name": "  빈  "})
    assert names(client, a, t)[0]["placeName"] == "빈"


def test_login_and_ownership(client, users, ext):
    a, b = users.signup("a@example.com"), users.signup("b@example.com")
    t = mk_trip(users, a)
    url = f"/api/trips/{t['id']}/day-cities"
    assert client.put(url, json={"from_day": 0, "to_day": 0, **VIENNA}).status_code == 401
    assert client.delete(url + "?from_day=0&to_day=0").status_code == 401
    assert put(client, b, t, 0, 0).status_code == 404         # 남의 여행은 없는 것처럼
    put(client, a, t, 0, 0)
    assert client.delete(url + "?from_day=0&to_day=0", headers=b).status_code == 404
    assert _fetch_count(t["id"]) == 1                         # 남이 지우지 못했다


def test_shared_weather_shows_the_users_city(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    put(client, a, t, 1, 2)
    token = client.post(f"/api/trips/{t['id']}/share", headers=a).json()["token"]
    client.cookies.clear()
    d = client.get(f"/api/shared/{token}/weather").json()["days"]
    assert [x["placeName"] for x in d] == ["프라하", "빈", "빈"]


def test_shortening_the_trip_ignores_days_beyond_it(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    put(client, a, t, 2, 2)
    r = client.patch(f"/api/trips/{t['id']}", json={"end_date": (TODAY + timedelta(days=1)).isoformat()}, headers=a)
    assert r.status_code == 200, r.text
    d = names(client, a, t)
    assert len(d) == 2 and [x["placeName"] for x in d] == ["프라하", "프라하"]
    r = put(client, a, t, 2, 2)                               # 이제 없는 일차는 정할 수 없다
    assert r.status_code == 422 and r.json()["detail"] == "day_out_of_range"


def test_deleting_the_trip_removes_the_cities(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    put(client, a, t, 0, 2)
    assert client.delete(f"/api/trips/{t['id']}", headers=a).status_code == 204
    assert _fetch_count(t["id"]) == 0


# ---------- 도시 검색 ----------
def test_city_search(client, users, monkeypatch):
    calls = []

    async def fake(q, lang="ko", limit=8):
        calls.append((q, lang))
        return [{"id": "1", "name": "빈", "country": "오스트리아", "admin1": None, "lat": 48.2, "lng": 16.4}]

    monkeypatch.setattr(openmeteo, "search_cities", fake)
    a = users.signup("a@example.com")
    assert client.get("/api/cities/search?q=빈").status_code == 401
    assert client.get("/api/cities/search?q=", headers=a).status_code == 422
    assert client.get("/api/cities/search?q=%20%20", headers=a).status_code == 400     # 공백뿐이면 검색하지 않는다
    r = client.get("/api/cities/search?q=  빈  &lang=ko", headers=a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"][0] == {"id": "1", "name": "빈", "country": "오스트리아", "admin1": None, "lat": 48.2, "lng": 16.4}
    assert "Open-Meteo" in body["attribution"]
    client.get("/api/cities/search?q=Wien&lang=xx", headers=a)                           # 모르는 언어는 ko
    assert calls == [("빈", "ko"), ("Wien", "ko")]


def test_city_search_upstream_error_and_rate_limit(client, users, monkeypatch):
    async def down(q, lang="ko", limit=8):
        raise ProviderError("status_500")

    monkeypatch.setattr(openmeteo, "search_cities", down)
    a = users.signup("a@example.com")
    r = client.get("/api/cities/search?q=빈", headers=a)
    assert r.status_code == 502 and r.json()["detail"] == "upstream_error"

    for _ in range(day_cities._search_limit.limit):
        day_cities._search_limit.add(str(client.get("/api/auth/me", headers=a).json()["id"]))
    assert client.get("/api/cities/search?q=빈", headers=a).status_code == 429


def _fetch_count(trip_id) -> int:
    from tests.conftest import _fetch
    return _fetch("select count(*) as n from trip_day_cities where trip_id = $1::uuid", trip_id)[0]["n"]
