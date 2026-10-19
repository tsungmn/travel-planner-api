from app.sharing import hide_lodging_stops

LODGING = {"lat": 36.07, "lng": 120.38, "provider_place_id": "kakao:hotel"}


def _stop(sid, order, lat, lng, place_id=None, memo=None):
    return {
        "id": sid, "trip_id": "t", "day_index": 0, "sort_order": order,
        "name": f"장소{sid}", "alias": None, "address_text": "주소", "lat": lat, "lng": lng,
        "provider": "kakao", "provider_place_id": place_id, "memo": memo,
    }


def test_lodging_stop_is_reduced_to_a_placeholder_keeping_order():
    stops = [_stop("a", 0, 36.0, 120.0), _stop("h", 1, 36.07, 120.38, memo="방 번호"), _stop("b", 2, 36.1, 120.1)]
    out = hide_lodging_stops(stops, [LODGING])

    assert [s["id"] for s in out] == ["a", "h", "b"]            # 순서(숙소 자리)는 그대로
    hidden = out[1]
    assert hidden["hidden"] is True
    assert all(hidden[k] is None for k in (
        "name", "alias", "address_text", "lat", "lng", "provider", "provider_place_id", "memo"
    ))
    assert out[0]["name"] == "장소a" and out[2]["lat"] == 36.1  # 나머지는 건드리지 않는다


def test_matches_lodging_by_place_id_even_when_coordinates_differ():
    out = hide_lodging_stops([_stop("h", 0, 1.0, 2.0, place_id="kakao:hotel")], [LODGING])
    assert out[0]["hidden"] is True


def test_no_lodgings_means_nothing_is_hidden():
    stops = [_stop("a", 0, 36.07, 120.38)]
    assert hide_lodging_stops(stops, []) == stops


def test_stop_without_place_id_is_not_matched_by_a_null_place_id():
    lodging = {"lat": 1.0, "lng": 1.0, "provider_place_id": None}
    out = hide_lodging_stops([_stop("a", 0, 9.0, 9.0, place_id=None)], [lodging])
    assert out[0].get("hidden") is None and out[0]["name"] == "장소a"
