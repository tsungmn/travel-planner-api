"""일차별 날씨 기준 위치 추정. 순수 함수라서 DB·네트워크 없이 테스트할 수 있다.

위치를 정하는 우선순위 (위에서부터 처음 걸리는 것):
  1. 사용자가 직접 정한 도시
  2. 그날 장소들의 대표 위치 (가운데 장소)
  3. 그날 러프 영역의 중심
  4. 그날 밤 묵는 숙소 (체크인 ≤ 그날 < 체크아웃)
  5. 전날의 위치
  6. 여행 목적지 좌표
  7. 모두 없으면 None (그 날은 날씨를 보여 주지 않는다)

일차 번호(day_index)는 0부터 시작한다. 도시 *이름*은 여기서 정하지 않는다: 사용자가 정한 도시만
이름이 있고, 나머지는 좌표만 돌려주며 이름은 나중에 좌표로 조회한다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, Mapping, Sequence

Source = Literal["user", "stops", "area", "lodging", "previous", "destination"]
Point = tuple[float, float]   # (lat, lng)


@dataclass(frozen=True)
class DayLocation:
    lat: float
    lng: float
    source: Source
    name: str | None = None   # 사용자가 정한 도시(와 그것을 이어받은 날)만 이름이 있다


def _km(a: Point, b: Point) -> float:
    """두 지점 사이의 직선 거리(km). 하버사인."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def central_point(points: Sequence[Point]) -> Point:
    """가운데 지점: 다른 모든 점까지의 거리 합이 가장 작은 점(medoid)을 고른다.

    좌표별 중앙값을 쓰면 장소가 두 도시에 나뉘었을 때 둘의 한가운데(아무 도시도 아닌 곳)가 나올 수
    있다. 실제 장소 중 하나를 고르면 한두 곳이 멀리 떨어져 있어도 흔들리지 않고, 항상 어느 도시
    안의 점이다. 동점이면 앞선 점.
    """
    if len(points) == 1:
        return points[0]
    best = min(range(len(points)), key=lambda i: (sum(_km(points[i], p) for p in points), i))
    return points[best]


def _stays_on(lodging: Mapping[str, Any], day: date) -> bool:
    """그 날 밤을 이 숙소에서 자는지. 퇴실일은 떠나는 날이라 포함하지 않는다."""
    check_in, check_out = lodging["check_in"], lodging["check_out"]
    if check_in is None:
        return False
    if check_out is None:
        return check_in == day       # 퇴실일을 모르면 체크인한 날만
    return check_in <= day < check_out


def resolve_day_locations(
    *,
    start_date: date,
    num_days: int,
    stops: Sequence[Mapping[str, Any]],
    areas: Sequence[Mapping[str, Any]],
    lodgings: Sequence[Mapping[str, Any]],
    destination: Point | None,
    overrides: Mapping[int, tuple[float, float, str]] | None = None,
) -> list[DayLocation | None]:
    """일차마다(0 … num_days-1) 날씨를 볼 위치를 돌려준다.

    overrides: {day_index: (lat, lng, 도시 이름)} — 사용자가 직접 정한 도시.
    """
    overrides = overrides or {}
    stops_by_day: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for s in sorted(stops, key=lambda s: s["sort_order"]):
        stops_by_day[s["day_index"]].append(s)
    areas_by_day: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for a in areas:
        if a["day_index"] is not None:     # 일차 미지정 영역은 어느 날의 근거도 아니다
            areas_by_day[a["day_index"]].append(a)

    result: list[DayLocation | None] = []
    for d in range(num_days):
        day = start_date + timedelta(days=d)
        prev = result[d - 1] if d > 0 else None
        loc: DayLocation | None = None

        if d in overrides:
            lat, lng, name = overrides[d]
            loc = DayLocation(lat, lng, "user", name)
        elif stops_by_day[d]:
            lat, lng = central_point([(s["lat"], s["lng"]) for s in stops_by_day[d]])
            loc = DayLocation(lat, lng, "stops")
        elif areas_by_day[d]:
            lat, lng = central_point([(a["lat"], a["lng"]) for a in areas_by_day[d]])
            loc = DayLocation(lat, lng, "area")
        else:
            staying = [l for l in lodgings if _stays_on(l, day)]
            if staying:
                # 겹치면 가장 늦게 체크인한 숙소(이동일에는 새 숙소가 맞다). 동점이면 먼저 온 것.
                chosen = max(enumerate(staying), key=lambda t: (t[1]["check_in"], -t[0]))[1]
                loc = DayLocation(chosen["lat"], chosen["lng"], "lodging")
            elif prev is not None:
                loc = DayLocation(prev.lat, prev.lng, "previous", prev.name)
            elif destination is not None:
                loc = DayLocation(destination[0], destination[1], "destination")
        result.append(loc)
    return result
