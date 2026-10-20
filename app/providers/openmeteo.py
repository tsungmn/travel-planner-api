"""Open-Meteo 일별 예보. API 키가 필요 없다. (무료 공개 API는 비상업 용도 — 서비스가 커지면 유료 플랜 검토)
https://open-meteo.com/en/docs
"""
from datetime import date

import httpx

from . import ProviderError

PROVIDER = "open-meteo"
ATTRIBUTION = "Weather data by Open-Meteo.com"
URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

FORECAST_DAYS = 16      # 무료 API가 주는 최대 일수
PAST_DAYS = 3           # 여행 중인 사람이 이미 지난 일차도 볼 수 있게

client = httpx.AsyncClient(timeout=10)   # main.py 의 lifespan 에서 종료


async def close() -> None:
    await client.aclose()


# WMO 날씨 코드 → (UI condition, 한국어 라벨). 표에 없는 코드는 추측하지 않고 None(그 날은 숨김)으로 둔다.
_WMO: dict[int, tuple[str, str]] = {
    0: ("clear", "맑음"),
    1: ("mostly-clear", "대체로 맑음"),
    2: ("partly-cloudy", "구름 조금"),
    3: ("cloudy", "흐림"),
    45: ("fog", "안개"), 48: ("fog", "안개"),
    51: ("drizzle", "이슬비"), 53: ("drizzle", "이슬비"), 55: ("drizzle", "이슬비"),
    56: ("drizzle", "어는 이슬비"), 57: ("drizzle", "어는 이슬비"),
    61: ("rain", "비"), 63: ("rain", "비"), 65: ("rain", "많은 비"),
    66: ("rain", "어는 비"), 67: ("rain", "어는 비"),
    71: ("snow", "눈"), 73: ("snow", "눈"), 75: ("snow", "많은 눈"), 77: ("snow", "싸락눈"),
    80: ("showers", "소나기"), 81: ("showers", "소나기"), 82: ("showers", "강한 소나기"),
    85: ("snow", "눈보라"), 86: ("snow", "눈보라"),
    95: ("thunderstorm", "천둥번개"), 96: ("thunderstorm", "천둥번개"), 99: ("thunderstorm", "천둥번개"),
}


def condition_for(code: int) -> tuple[str, str] | None:
    return _WMO.get(code)


def parse_daily(data: object) -> list[dict]:
    """API 응답에서 일별 예보를 꺼낸다. 값이 빠진 날(코드나 기온이 null)은 건너뛴다.

    반환: [{"date": "YYYY-MM-DD", "code": int, "tmin": float, "tmax": float, "precip": int | None}]
    """
    try:
        daily = data["daily"]            # type: ignore[index]
        times = daily["time"]
        codes = daily["weather_code"]
        tmax = daily["temperature_2m_max"]
        tmin = daily["temperature_2m_min"]
        precip = daily.get("precipitation_probability_max") or [None] * len(times)
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("bad_shape") from None
    if not all(isinstance(x, list) for x in (times, codes, tmax, tmin, precip)):
        raise ProviderError("bad_shape")
    if not (len(times) == len(codes) == len(tmax) == len(tmin) == len(precip)):
        raise ProviderError("bad_shape")

    out: list[dict] = []
    for t, c, hi, lo, p in zip(times, codes, tmax, tmin, precip):
        try:
            date.fromisoformat(t)
            if c is None or hi is None or lo is None:
                continue
            code, hi_f, lo_f = int(c), float(hi), float(lo)
            p_i = None if p is None else max(0, min(100, int(round(float(p)))))
        except (TypeError, ValueError):
            continue
        out.append({"date": t, "code": code, "tmin": lo_f, "tmax": hi_f, "precip": p_i})
    return out


async def fetch_daily(lat: float, lng: float) -> list[dict]:
    """한 지점의 일별 예보(지난 PAST_DAYS일 + 앞으로 FORECAST_DAYS일). 날짜는 그 위치의 현지 날짜."""
    params: dict[str, str | int | float] = {
        "latitude": round(lat, 4),
        "longitude": round(lng, 4),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": FORECAST_DAYS,
        "past_days": PAST_DAYS,
    }
    try:
        r = await client.get(URL, params=params)
    except httpx.HTTPError:
        raise ProviderError("network") from None
    if r.status_code != 200:
        raise ProviderError(f"status_{r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise ProviderError("bad_json") from None
    return parse_daily(data)


def parse_cities(data: object, limit: int = 8) -> list[dict]:
    """도시 검색 응답에서 후보를 꺼낸다. 이름이나 좌표가 빠진 항목은 건너뛴다. 결과가 없으면 키가 없어서 빈 목록이다.

    반환: [{"id": str, "name": str, "country": str | None, "admin1": str | None, "lat": float, "lng": float}]
    """
    if not isinstance(data, dict):
        raise ProviderError("bad_shape")
    results = data.get("results", [])
    if not isinstance(results, list):
        raise ProviderError("bad_shape")
    out: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        try:
            name = (r.get("name") or "").strip()
            lat, lng = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not name or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        out.append({
            "id": str(r.get("id") or f"{lat:.4f},{lng:.4f}"),
            "name": name[:100],
            "country": ((r.get("country") or "").strip() or None),
            "admin1": ((r.get("admin1") or "").strip() or None),
            "lat": lat,
            "lng": lng,
        })
        if len(out) >= limit:
            break
    return out


async def search_cities(q: str, lang: str = "ko", limit: int = 8) -> list[dict]:
    """도시 이름으로 검색한다. 이름은 lang 으로 받고, 그 언어 이름이 없으면 현지어가 온다."""
    params: dict[str, str | int] = {"name": q, "count": limit, "language": lang, "format": "json"}
    try:
        r = await client.get(GEOCODING_URL, params=params)
    except httpx.HTTPError:
        raise ProviderError("network") from None
    if r.status_code != 200:
        raise ProviderError(f"status_{r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise ProviderError("bad_json") from None
    return parse_cities(data, limit)
