import pytest


def test_requires_login(client):
    assert client.get("/api/trips").status_code == 401
    assert client.post("/api/trips", json={}).status_code == 401


def test_cannot_touch_someone_elses_trip(client, users):
    a, b = users.signup("a@example.com"), users.signup("b@example.com")
    trip = users.trip(a)
    stop = users.stop(a, trip["id"])
    tid, sid = trip["id"], stop["id"]

    # 다른 사용자에게는 존재 여부도 드러나지 않는다 (403 이 아니라 404)
    assert client.get(f"/api/trips/{tid}", headers=b).status_code == 404
    assert client.patch(f"/api/trips/{tid}", json={"title": "x"}, headers=b).status_code == 404
    assert client.delete(f"/api/trips/{tid}", headers=b).status_code == 404
    assert client.post(f"/api/trips/{tid}/share", headers=b).status_code == 404
    assert client.post(
        f"/api/trips/{tid}/stops", json={"day_index": 0, "name": "x", "lat": 1, "lng": 1}, headers=b
    ).status_code == 404
    assert client.patch(f"/api/trips/{tid}/stops/{sid}", json={"name": "x"}, headers=b).status_code == 404
    assert client.delete(f"/api/trips/{tid}/stops/{sid}", headers=b).status_code == 404
    assert client.get("/api/trips", headers=b).json() == []

    # 원래 주인의 데이터는 그대로
    got = client.get(f"/api/trips/{tid}", headers=a).json()
    assert got["title"] == "칭다오" and [s["name"] for s in got["stops"]] == ["잔교"]


def test_stop_of_another_trip_cannot_be_edited_through_my_trip(client, users):
    a, b = users.signup("a@example.com"), users.signup("b@example.com")
    ta, tb = users.trip(a), users.trip(b)
    sa = users.stop(a, ta["id"])
    # B 가 자기 여행 경로에 A 의 stop id 를 끼워 넣어도 404
    assert client.patch(f"/api/trips/{tb['id']}/stops/{sa['id']}", json={"name": "hack"}, headers=b).status_code == 404
    assert client.delete(f"/api/trips/{tb['id']}/stops/{sa['id']}", headers=b).status_code == 404
    assert client.get(f"/api/trips/{ta['id']}", headers=a).json()["stops"][0]["name"] == "잔교"


def test_reorder_is_all_or_nothing(client, users):
    a, b = users.signup("a@example.com"), users.signup("b@example.com")
    ta, tb = users.trip(a), users.trip(b)
    s1, s2 = users.stop(a, ta["id"], 0, "하나"), users.stop(a, ta["id"], 0, "둘")
    foreign = users.stop(b, tb["id"])

    url = f"/api/trips/{ta['id']}/stops/order"
    ok = [{"id": s1["id"], "day_index": 1, "sort_order": 0}, {"id": s2["id"], "day_index": 1, "sort_order": 1}]
    assert client.put(url, json=ok, headers=a).status_code == 204

    # 남의 장소가 하나 섞이면 전체 취소 (앞의 정상 항목도 반영되지 않는다)
    bad = [{"id": s1["id"], "day_index": 2, "sort_order": 5}, {"id": foreign["id"], "day_index": 0, "sort_order": 0}]
    r = client.put(url, json=bad, headers=a)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_stop"
    stops = {s["id"]: s for s in client.get(f"/api/trips/{ta['id']}", headers=a).json()["stops"]}
    assert (stops[s1["id"]]["day_index"], stops[s1["id"]]["sort_order"]) == (1, 0)

    dup = [ok[0], ok[0]]
    assert client.put(url, json=dup, headers=a).json()["detail"] == "duplicate_stop"
    out = [{"id": s1["id"], "day_index": 3, "sort_order": 0}]       # 여행은 3일(0~2)
    assert client.put(url, json=out, headers=a).json()["detail"] == "day_out_of_range"


def test_shrinking_trip_is_rejected_while_stops_would_be_orphaned(client, users):
    a = users.signup("a@example.com")
    t = users.trip(a)
    users.stop(a, t["id"], day=2)
    r = client.patch(f"/api/trips/{t['id']}", json={"end_date": "2026-11-02"}, headers=a)
    assert r.status_code == 409 and r.json()["detail"] == "stops_out_of_range"
    assert client.patch(f"/api/trips/{t['id']}", json={"end_date": "2026-10-01"}, headers=a).json()["detail"] == "invalid_dates"
    assert client.patch(f"/api/trips/{t['id']}", json={"title": None}, headers=a).json()["detail"] == "title_cannot_be_null"
    assert client.patch(f"/api/trips/{t['id']}", json={}, headers=a).json()["detail"] == "no_fields"


@pytest.mark.parametrize("days,ok", [(61, True), (62, False)])
def test_trip_length_limit(client, users, days, ok):
    from datetime import date, timedelta
    a = users.signup("a@example.com")
    start = date(2026, 11, 1)
    r = client.post(
        "/api/trips",
        json={"title": "t", "destination": "d", "start_date": str(start), "end_date": str(start + timedelta(days=days - 1))},
        headers=a,
    )
    assert (r.status_code == 201) is ok


def test_share_link_lifecycle(client, users):
    a = users.signup("a@example.com")
    t = users.trip(a)
    users.stop(a, t["id"])
    assert client.get("/api/shared/nope").status_code == 404

    token = client.post(f"/api/trips/{t['id']}/share", headers=a).json()["token"]
    assert len(token) == 64
    assert client.post(f"/api/trips/{t['id']}/share", headers=a).json()["token"] == token   # 이미 켜져 있으면 유지

    client.cookies.clear()                      # 비로그인
    shared = client.get(f"/api/shared/{token}")
    assert shared.status_code == 200
    body = shared.json()
    assert body["trip"]["title"] == "칭다오" and len(body["stops"]) == 1
    assert "owner_id" not in body["trip"] and "share_token" not in body["trip"]

    assert client.delete(f"/api/trips/{t['id']}/share", headers=a).status_code == 204
    assert client.get(f"/api/shared/{token}").status_code == 404