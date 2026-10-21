"""실제 Open-Meteo(예보·과거 기록)에 붙어서 날씨 흐름을 시나리오별로 확인한다. DB·로그인 없이 돈다.

    python -m scripts.verify_weather                       # 프라하 기준
    python -m scripts.verify_weather --lat 37.5665 --lng 126.978 --name 서울

시나리오: 가까운 날짜 / 먼 날짜 / 예보 경계에 걸친 여행 / 위치 없음 / 지난 여행 / Open-Meteo 차단.
각 시나리오의 날씨와 외부 요청 수를 보여 주고, 단위·형식 같은 \"실제 응답이어야만 알 수 있는\" 것은 CHECK 줄로 따진다.
마지막에 FAIL 이 하나라도 있으면 종료 코드 1이다. (무료 공개 API 를 쓰니 몇 번 이상 반복해서 돌리지 않는다)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime, timedelta, timezone

from app.climate import NormalsService
from app.providers import ProviderError, openmeteo
from app.trip_weather import compose_trip_weather
from app.weather import ForecastService, grid_key
from app.weather_location import DayLocation

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"  {'CHECK ok  ' if ok else 'CHECK FAIL'} {what}")
    if not ok:
        failures.append(what)


class MemForecastStore:
    def __init__(self):
        self.rows = {}

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, days, now):
        self.rows[key] = (now, days)


class MemClimateStore:
    def __init__(self):
        self.rows = {}

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, through_year, normals, now):
        self.rows[key] = (through_year, normals)


class NoNames:
    async def names(self, points):
        return {}


class Counter:
    """외부 요청 수를 센다. (서비스에 넣는 fetch 를 감싼다)"""

    def __init__(self):
        self.forecast = self.history = 0

    async def fetch_daily(self, lat, lng):
        self.forecast += 1
        return await openmeteo.fetch_daily(lat, lng)

    async def fetch_history(self, lat, lng, start, end):
        self.history += 1
        return await openmeteo.fetch_history(lat, lng, start, end)


def line(d: dict) -> str:
    if d["source"] == "forecast":
        extra = []
        if d["rainy"]:
            extra.append(f"비 {d['precipMm']}mm {d['rainPeriod'] or '시간대?'}")
        if d["windMax"] is not None and d["windMax"] >= 8:
            extra.append(f"강풍 {d['windMax']}m/s")
        return (f"{d['date']} 예보({d['confidence']:6}) {d['condition']:13} {d['tempMin']:>3}°~{d['tempMax']:>3}°"
                f"  체감 {d['feelsMin']}~{d['feelsMax']}  일출/일몰 {d['sunrise']}/{d['sunset']}  {' '.join(extra)}")
    n = d["normal"]
    return (f"{d['date']} 평년({d['confidence']:6}) {'':13} {d['tempMin']:>3}°~{d['tempMax']:>3}°"
            f"  추운날 {n['coldTemp']}° 따뜻한날 {n['hotTemp']}°  비 온 날 {n['rainyRatio']:.0%}"
            f"{' (가능성 높음)' if n['rainLikely'] else ''}  [{n['fallbackReason']}]")


async def scenario(title, *, offset, n, point, today, blocked=False, locations=None):
    print(f"\n■ {title}")
    counter = Counter()
    forecast = ForecastService(MemForecastStore(), counter.fetch_daily)
    normals = NormalsService(MemClimateStore(), counter.fetch_history, today=lambda: today)
    locs = locations if locations is not None else [DayLocation(point[0], point[1], "destination")] * n
    t0 = time.monotonic()
    out = await compose_trip_weather(
        start_date=today + timedelta(days=offset), locations=locs, forecast=forecast, normals=normals,
        names=NoNames(), today=today)
    print(f"  {time.monotonic() - t0:.1f}초 · 예보 요청 {counter.forecast}회 · 평년 요청 {counter.history}회"
          f" · fetchedAt={out['fetchedAt']} stale={out['stale']}")
    for d in out["days"]:
        print("  " + line(d))
    if out["summary"]:
        print(f"  요약: {out['summary']}")
    else:
        print("  (날씨 없음)")
    return out, counter


async def main(lat: float, lng: float, name: str) -> int:
    today = datetime.now(timezone.utc).date()
    point = (lat, lng)
    print(f"기준 위치: {name} ({lat}, {lng}) · 오늘(UTC) {today}")

    out, c = await scenario("가까운 날짜 (오늘부터 3일)", offset=0, n=3, point=point, today=today)
    days = out["days"]
    check(len(days) == 3 and all(d["source"] == "forecast" for d in days), "3일 모두 예보")
    check(c.history == 0, "평년은 요청하지 않았다")
    check(all(d["windMax"] is not None and 0 <= d["windMax"] < 40 for d in days),
          "풍속이 m/s 범위(0~40) — km/h 로 오면 30 이 넘는 값이 흔하다")
    check(all(d["sunrise"] and d["sunset"] and d["sunrise"] < d["sunset"] for d in days), "일출·일몰이 HH:MM 이고 일출이 먼저")
    check(all(d["feelsMin"] is not None and d["feelsMax"] is not None for d in days), "체감온도가 있다")
    rainy = [d for d in days if d["rainy"]]
    check(all(d["rainPeriod"] in ("morning", "afternoon", "both") for d in rainy),
          f"비 오는 날({len(rainy)}일)은 오전/오후가 정해진다" if rainy else "비 오는 날 없음 — 오전/오후는 확인할 수 없다(비 오는 날 다시 돌려 보기)")

    out, c = await scenario("먼 날짜 (오늘+40일부터 3일)", offset=40, n=3, point=point, today=today)
    days = out["days"]
    check(len(days) == 3 and all(d["source"] == "normal" for d in days), "3일 모두 평년")
    check(c.forecast == 0 and c.history == 1, "예보는 요청하지 않고 평년 기록은 한 번만 받았다")
    check(all(d["tempMin"] <= d["tempMax"] and d["normal"]["coldTemp"] <= d["tempMin"] and d["tempMax"] <= d["normal"]["hotTemp"]
              for d in days), "추운 날 ≤ 보통 최저 ≤ 보통 최고 ≤ 따뜻한 날")
    check(all(0 <= d["normal"]["rainyRatio"] <= 1 for d in days), "비 온 날 비율이 0~1")

    out, c = await scenario("예보 경계에 걸친 여행 (오늘+14일부터 5일)", offset=14, n=5, point=point, today=today)
    check([d["source"] for d in out["days"]] == ["forecast", "forecast", "normal", "normal", "normal"], "예보 2일 + 평년 3일")
    check(out["summary"] and out["summary"]["basis"] == "mixed" and out["summary"]["firstNormalDay"] == 3, "요약은 혼합, 3일차부터 평년")

    out, c = await scenario("위치 없음", offset=0, n=3, point=None, today=today, locations=[None, None, None])
    check(out["days"] == [] and c.forecast == 0 and c.history == 0, "날씨도 외부 요청도 없다")

    out, c = await scenario("지난 여행 (30일 전)", offset=-30, n=3, point=point, today=today)
    check(out["days"] == [] and c.forecast == 0 and c.history == 0, "날씨도 외부 요청도 없다")

    # 차단: 닿지 않는 주소로 돌린다 (연결 거부)
    saved = openmeteo.URL, openmeteo.ARCHIVE_URL
    openmeteo.URL = openmeteo.ARCHIVE_URL = "http://127.0.0.1:9/blocked"
    try:
        out, c = await scenario("Open-Meteo 차단 (가까운 여행)", offset=0, n=3, point=point, today=today)
        check(out["days"] == [] and out["summary"] is None, "예외 없이 빈 응답")
    finally:
        openmeteo.URL, openmeteo.ARCHIVE_URL = saved

    # 평년의 "비 온 날" 기준이 예보와 같은지: 같은 해 같은 곳의 rain_sum 과 precipitation_sum 을 비교한다
    print("\n■ rain_sum 의미 확인 (지난해 한 해)")
    last = today.year - 1
    data = await _raw_archive(lat, lng, date(last, 1, 1), date(last, 12, 31))
    rain, total = _sum(data, "rain_sum"), _sum(data, "precipitation_sum")
    print(f"  rain_sum 합 {rain:.0f}mm · precipitation_sum 합 {total:.0f}mm (눈이 많은 곳일수록 차이가 크다)")
    check(0 < rain <= total + 1, "rain_sum 이 있고 precipitation_sum 을 넘지 않는다 (눈을 뺀 값으로 보인다)")

    print(f"\n{'모든 확인 통과' if not failures else f'실패 {len(failures)}건: ' + '; '.join(failures)}")
    return 1 if failures else 0


async def _raw_archive(lat, lng, start, end):
    params = {"latitude": lat, "longitude": lng, "start_date": start.isoformat(), "end_date": end.isoformat(),
              "daily": "rain_sum,precipitation_sum", "timezone": "auto"}
    r = await openmeteo.client.get(openmeteo.ARCHIVE_URL, params=params, timeout=openmeteo.HISTORY_TIMEOUT)
    if r.status_code != 200:
        raise ProviderError(f"status_{r.status_code}")
    return r.json()


def _sum(data, key):
    return sum(v for v in data["daily"][key] if v is not None)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=50.0755)
    ap.add_argument("--lng", type=float, default=14.4378)
    ap.add_argument("--name", default="프라하")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.lat, a.lng, a.name)))
