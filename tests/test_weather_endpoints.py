"""날씨 엔드포인트. 외부 API(Open-Meteo, Geoapify)는 가짜로 바꿔 끼운다. (TEST_DATABASE_URL 필요)"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.providers import ProviderError, geoapify, openmeteo
from tests.conftest import _run

TODAY = datetime.now(timezone.utc).date()


def fake_days(lat):
    return [{"date": (TODAY + timedelta(days=i)).isoformat(), "code": 61 if lat > 49 else 0,
             "tmin": 2.0, "tmax": 9.0, "precip": 55} for i in range(-3, 16)]


@pytest.fixture()
def ext(monkeypatch):
    """외부 호출을 기록하고, 위도 49 위는 프라하, 아래는 빈으로 답한다."""
    calls = {"forecast": [], "city": []}

    async def fetch(lat, lng):
        calls["forecast"].append((lat, lng))
        return fake_days(lat)

    async def city(lat, lng, lang="ko"):
        calls["city"].append((lat, lng))
        return "프라하" if lat > 49 else "빈"

    monkeypatch.setattr(openmeteo, "fetch_daily", fetch)
    monkeypatch.setattr(geoapify, "reverse_city", city)
    monkeypatch.setattr(settings, "geoapify_api_key", "test-key")
    _run("truncate weather_cache, place_names")
    return calls


def mk_trip(users, h, days=3, **over):
    return users.trip(h, start_date=TODAY.isoformat(), end_date=(TODAY + timedelta(days=days - 1)).isoformat(),
                      dest_lat=50.0755, dest_lng=14.4378, destination="Prague", **over)


def add_stop(client, h, t, day, lat, lng, name="장소"):
    r = client.post(f"/api/trips/{t['id']}/stops", json={"day_index": day, "name": name, "lat": lat, "lng": lng}, headers=h)
    assert r.status_code == 201, r.text


def test_requires_login_and_owner(client, users, ext):
    a, b = users.signup("a@example.com"), users.signup("b@example.com")
    t = mk_trip(users, a)
    assert client.get(f"/api/trips/{t['id']}/weather").status_code == 401
    assert client.get(f"/api/trips/{t['id']}/weather", headers=b).status_code == 404


def test_multi_city_trip_follows_stops_and_marks_city_change(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    add_stop(client, a, t, 0, 50.0755, 14.4378)
    add_stop(client, a, t, 2, 48.2082, 16.3738)          # 1일차는 비어 있다 → 전날(프라하)

    r = client.get(f"/api/trips/{t['id']}/weather", headers=a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attribution"] == openmeteo.ATTRIBUTION
    d = body["days"]
    assert [x["placeName"] for x in d] == ["프라하", "프라하", "빈"]
    assert [x["placeChangedFromPrev"] for x in d] == [False, False, True]
    assert [x["condition"] for x in d] == ["rain", "rain", "clear"]
    assert d[0]["date"] == TODAY.isoformat() and d[0]["precipProbability"] == 55 and d[0]["placeSource"] == "auto"
    assert set(d[0]) == {"date", "condition", "label", "tempMin", "tempMax", "precipProbability", "uncertain",
                         "placeName", "placeSource", "placeChangedFromPrev", "transitFrom"}
    # 이동일(3일차)에만 전날 도시(프라하)의 같은 날 예보가 붙는다. 3일차 본인은 빈(clear)이다.
    assert [x["transitFrom"] is not None for x in d] == [False, False, True]
    tf = d[2]["transitFrom"]
    assert (tf["placeName"], tf["condition"], tf["date"]) == ("프라하", "rain", d[2]["date"])
    assert tf["placeChangedFromPrev"] is False


def test_second_request_uses_cache(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    add_stop(client, a, t, 0, 50.0755, 14.4378)
    client.get(f"/api/trips/{t['id']}/weather", headers=a)
    n_f, n_c = len(ext["forecast"]), len(ext["city"])
    assert n_f >= 1 and n_c >= 1
    client.get(f"/api/trips/{t['id']}/weather", headers=a)
    assert (len(ext["forecast"]), len(ext["city"])) == (n_f, n_c)


def test_trip_without_any_coordinates_has_no_weather(client, users, ext):
    a = users.signup("a@example.com")
    t = users.trip(a, start_date=TODAY.isoformat(), end_date=TODAY.isoformat())    # 목적지 좌표도 장소도 없다
    assert client.get(f"/api/trips/{t['id']}/weather", headers=a).json()["days"] == []
    assert ext["forecast"] == []


def test_far_future_trip_makes_no_external_requests(client, users, ext):
    a = users.signup("a@example.com")
    t = users.trip(a, start_date=(TODAY + timedelta(days=40)).isoformat(), end_date=(TODAY + timedelta(days=42)).isoformat(),
                   dest_lat=50.0, dest_lng=14.0)
    assert client.get(f"/api/trips/{t['id']}/weather", headers=a).json()["days"] == []
    assert ext["forecast"] == [] and ext["city"] == []


def test_provider_outage_gives_empty_days_not_an_error(client, users, monkeypatch, ext):
    async def down(lat, lng):
        raise ProviderError("network")

    monkeypatch.setattr(openmeteo, "fetch_daily", down)
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    r = client.get(f"/api/trips/{t['id']}/weather", headers=a)
    assert r.status_code == 200 and r.json()["days"] == []


def test_name_lookup_failure_keeps_the_weather(client, users, monkeypatch, ext):
    async def down(lat, lng, lang="ko"):
        raise ProviderError("status_429")

    monkeypatch.setattr(geoapify, "reverse_city", down)
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    d = client.get(f"/api/trips/{t['id']}/weather", headers=a).json()["days"]
    assert len(d) == 3 and all(x["placeName"] is None and x["placeChangedFromPrev"] is False for x in d)


def test_shared_weather_is_public_but_not_found_for_bad_token(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)
    add_stop(client, a, t, 0, 50.0755, 14.4378)
    token = client.post(f"/api/trips/{t['id']}/share", headers=a).json()["token"]
    client.cookies.clear()
    r = client.get(f"/api/shared/{token}/weather")
    assert r.status_code == 200 and len(r.json()["days"]) == 3
    assert client.get("/api/shared/nope/weather").status_code == 404
    assert client.get(f"/api/shared/{'x' * 101}/weather").status_code == 404


def test_shared_weather_does_not_use_hidden_lodging(client, users, ext):
    a = users.signup("a@example.com")
    t = mk_trip(users, a)       # 목적지는 프라하
    # 1일차는 빈에 숙소(체크인 1일차) — 숙소를 공개하지 않으면 도시 이름으로 새면 안 된다
    day1 = (TODAY + timedelta(days=1)).isoformat()
    assert client.post(f"/api/trips/{t['id']}/lodgings",
                       json={"name": "비밀 호텔", "lat": 48.2082, "lng": 16.3738, "check_in": day1,
                             "check_out": (TODAY + timedelta(days=3)).isoformat()}, headers=a).status_code == 201
    mine = client.get(f"/api/trips/{t['id']}/weather", headers=a).json()["days"]
    assert [x["placeName"] for x in mine] == ["프라하", "빈", "빈"]          # 내 화면은 숙소를 쓴다

    token = client.post(f"/api/trips/{t['id']}/share", headers=a).json()["token"]          # 숙소 비공개
    client.cookies.clear()
    hidden = client.get(f"/api/shared/{token}/weather").json()["days"]
    assert [x["placeName"] for x in hidden] == ["프라하"] * 3

    client.post(f"/api/trips/{t['id']}/share", json={"lodgings": True}, headers=a)         # 숙소 공개
    client.cookies.clear()
    shown = client.get(f"/api/shared/{token}/weather").json()["days"]
    assert [x["placeName"] for x in shown] == ["프라하", "빈", "빈"]
