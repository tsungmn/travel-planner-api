def _fill(client, users, h):
    """항공편·숙소·메모가 모두 있는 여행을 만든다."""
    t = users.trip(h)
    stop = client.post(
        f"/api/trips/{t['id']}/stops",
        json={"day_index": 0, "name": "잔교", "lat": 36.06, "lng": 120.31, "memo": "비공개 메모"},
        headers=h,
    )
    assert stop.status_code == 201, stop.text
    assert client.post(
        f"/api/trips/{t['id']}/flights", json={"direction": "outbound", "flight_no": "KE861"}, headers=h
    ).status_code == 201
    assert client.post(
        f"/api/trips/{t['id']}/lodgings",
        json={"name": "호텔", "lat": 36.07, "lng": 120.38, "memo": "방 번호 1203"},
        headers=h,
    ).status_code == 201
    assert client.post(
        f"/api/trips/{t['id']}/areas",
        json={"day_index": 0, "label": "구시가", "lat": 36.06, "lng": 120.31, "memo": "영역 메모"},
        headers=h,
    ).status_code == 201
    return t


def _view(client, token):
    client.cookies.clear()      # 비로그인으로 조회
    r = client.get(f"/api/shared/{token}")
    assert r.status_code == 200, r.text
    return r.json()


def test_new_link_shares_only_stops_and_areas(client, users):
    a = users.signup("a@example.com")
    t = _fill(client, users, a)

    r = client.post(f"/api/trips/{t['id']}/share", headers=a)           # 본문 없이 켜기 = 기본값
    assert r.status_code == 200
    assert {k: r.json()[k] for k in ("flights", "lodgings", "memos")} == {
        "flights": False, "lodgings": False, "memos": False,
    }

    body = _view(client, r.json()["token"])
    assert body["share"] == {"flights": False, "lodgings": False, "memos": False}
    assert body["flights"] == [] and body["lodgings"] == []
    assert [s["name"] for s in body["stops"]] == ["잔교"] and body["stops"][0]["memo"] is None
    assert [x["label"] for x in body["areas"]] == ["구시가"] and body["areas"][0]["memo"] is None


def test_each_option_opens_only_its_own_part(client, users):
    a = users.signup("a@example.com")
    t = _fill(client, users, a)
    url = f"/api/trips/{t['id']}/share"

    token = client.post(url, json={"flights": True}, headers=a).json()["token"]
    body = _view(client, token)
    assert [f["flight_no"] for f in body["flights"]] == ["KE861"]
    assert body["lodgings"] == [] and body["stops"][0]["memo"] is None

    client.post(url, json={"lodgings": True}, headers=a)                 # 보낸 항목만 바뀌고 나머지는 유지
    body = _view(client, token)
    assert body["share"] == {"flights": True, "lodgings": True, "memos": False}
    assert [x["name"] for x in body["lodgings"]] == ["호텔"] and body["lodgings"][0]["memo"] is None

    client.post(url, json={"memos": True}, headers=a)
    body = _view(client, token)
    assert body["lodgings"][0]["memo"] == "방 번호 1203"
    assert body["stops"][0]["memo"] == "비공개 메모" and body["areas"][0]["memo"] == "영역 메모"

    client.post(url, json={"flights": False}, headers=a)                 # 다시 끄기
    assert _view(client, token)["flights"] == []


def test_changing_options_keeps_the_same_token(client, users):
    a = users.signup("a@example.com")
    t = users.trip(a)
    url = f"/api/trips/{t['id']}/share"
    token = client.post(url, headers=a).json()["token"]
    assert client.post(url, json={"memos": True}, headers=a).json()["token"] == token
    assert client.post(url, headers=a).json()["memos"] is True           # 본문 없이 다시 호출해도 값 유지


def test_disable_resets_scope_and_reenable_starts_private(client, users):
    a = users.signup("a@example.com")
    t = _fill(client, users, a)
    url = f"/api/trips/{t['id']}/share"
    old = client.post(url, json={"flights": True, "lodgings": True, "memos": True}, headers=a).json()["token"]

    assert client.delete(url, headers=a).status_code == 204
    client.cookies.clear()
    assert client.get(f"/api/shared/{old}").status_code == 404

    again = client.post(url, headers=a).json()
    assert again["token"] != old
    assert (again["flights"], again["lodgings"], again["memos"]) == (False, False, False)
    assert _view(client, again["token"])["flights"] == []


def test_owner_sees_share_settings_on_the_trip(client, users):
    a = users.signup("a@example.com")
    t = users.trip(a)
    assert (t["share_flights"], t["share_lodgings"], t["share_memos"]) == (False, False, False)
    client.post(f"/api/trips/{t['id']}/share", json={"memos": True}, headers=a)
    got = client.get(f"/api/trips/{t['id']}", headers=a).json()
    assert got["share_memos"] is True and got["share_flights"] is False and got["share_token"]


def test_only_owner_can_change_share_options(client, users):
    a, b = users.signup("a@example.com"), users.signup("b@example.com")
    t = users.trip(a)
    assert client.post(f"/api/trips/{t['id']}/share", json={"memos": True}, headers=b).status_code == 404
    assert client.post(f"/api/trips/{t['id']}/share", json={"memos": "x"}, headers=a).status_code == 422

def test_lodging_stop_is_hidden_as_a_placeholder_when_lodgings_are_private(client, users):
    a = users.signup("a@example.com")
    t = _fill(client, users, a)
    # 숙소에서 만든 장소(숙소와 같은 좌표) 하나를 일정에 넣는다
    assert client.post(
        f"/api/trips/{t['id']}/stops",
        json={"day_index": 0, "name": "호텔", "address_text": "비밀 주소", "lat": 36.07, "lng": 120.38, "memo": "방 번호 1203"},
        headers=a,
    ).status_code == 201
    url = f"/api/trips/{t['id']}/share"
    token = client.post(url, headers=a).json()["token"]

    stops = _view(client, token)["stops"]
    assert [s["name"] for s in stops] == ["잔교", None]
    hidden = stops[1]
    assert hidden["hidden"] is True
    assert (hidden["lat"], hidden["lng"], hidden["address_text"], hidden["memo"]) == (None, None, None, None)

    client.post(url, json={"lodgings": True}, headers=a)               # 공개하면 그대로 보인다
    stops = _view(client, token)["stops"]
    assert [s["name"] for s in stops] == ["잔교", "호텔"] and not any(s["hidden"] for s in stops)
