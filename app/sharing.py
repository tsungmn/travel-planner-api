"""공유 링크 응답에서 비공개 항목을 걸러내는 순수 함수들 (DB 없이 테스트할 수 있다)."""


def _is_lodging_stop(stop: dict, lodgings: list[dict]) -> bool:
    """숙소에서 만든 장소인지. 숙소와 같은 좌표이거나 같은 장소 id 면 그렇다고 본다.
    (웹의 findLodgingFor 와 같은 규칙이다.)"""
    return any(
        (lodging["lat"] == stop["lat"] and lodging["lng"] == stop["lng"])
        or (
            lodging["provider_place_id"] is not None
            and lodging["provider_place_id"] == stop["provider_place_id"]
        )
        for lodging in lodgings
    )


def hide_lodging_stops(stops: list[dict], lodgings: list[dict]) -> list[dict]:
    """숙소를 공개하지 않을 때, 숙소에서 만든 장소를 자리표시로 바꾼다.

    숙소 목록만 비우면 같은 숙소가 '장소'로 이름·주소·좌표째 새어 나간다. 그렇다고 장소를 아예
    빼면 일정 흐름(A → 숙소 → B)이 끊겨 보이니, 순서만 남기고 나머지는 모두 비운다.
    """
    result = []
    for stop in stops:
        if _is_lodging_stop(stop, lodgings):
            result.append({
                "id": stop["id"], "trip_id": stop["trip_id"],
                "day_index": stop["day_index"], "sort_order": stop["sort_order"],
                "hidden": True,
                "name": None, "alias": None, "address_text": None,
                "lat": None, "lng": None,
                "provider": None, "provider_place_id": None, "memo": None,
            })
        else:
            result.append(stop)
    return result
