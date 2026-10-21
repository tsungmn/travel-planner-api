"""Open-Meteo 일별 예보. API 키가 필요 없다. (무료 공개 API는 비상업 용도 — 서비스가 커지면 유료 플랜 검토)
https://open-meteo.com/en/docs
"""
import math
from datetime import date

import httpx

from . import ProviderError

PROVIDER = "open-meteo"
ATTRIBUTION = "Weather data by Open-Meteo.com"
URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

FORECAST_DAYS = 16      # 무료 API가 주는 최대 일수
PAST_DAYS = 3           # 여행 중인 사람이 이미 지난 일차도 볼 수 있게

DAILY_FIELDS = ",".join((
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "apparent_temperature_max", "apparent_temperature_min",
    "precipitation_probability_max", "rain_sum", "showers_sum", "snowfall_sum",
    "wind_speed_10m_max", "sunrise", "sunset",
))
HOURLY_FIELDS = "rain,showers"     # 오전/오후 비를 가르는 데 쓴다 (일 합계는 daily 가 준다)
HISTORY_TIMEOUT = 30    # 10년치 일별 기록은 예보보다 크다

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


def _optional_series(daily: dict, key: str, n: int) -> list:
    """없어도 되는 필드. 키가 없거나 null 이면 전부 None 인 목록으로 본다."""
    v = daily.get(key)
    return [None] * n if v is None else v


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _hhmm(v: object) -> str | None:
    """"2026-11-01T06:45" → "06:45". 극지방처럼 해가 뜨지 않는 날은 null 이다."""
    if not isinstance(v, str) or len(v) < 16 or v[10] != "T":
        return None
    t = v[11:16]
    return t if t[2] == ":" and t[:2].isdigit() and t[3:].isdigit() else None


def _rain_by_half_day(data: object) -> dict[str, tuple[float, float]]:
    """시간별 비(rain + showers)를 날짜별 (오전 합, 오후 합)(mm)으로 묶는다. 오전은 0~11시, 오후는 12~23시.
    시간별 값은 덤이라서 모양이 이상하면 예외 대신 빈 결과를 돌려준다. (그러면 그날의 오전/오후만 모른다)"""
    try:
        hourly = data["hourly"]            # type: ignore[index]
        times, rain, showers = hourly["time"], hourly["rain"], hourly["showers"]
    except (KeyError, TypeError, AttributeError):
        return {}
    if not all(isinstance(x, list) for x in (times, rain, showers)) or not (len(times) == len(rain) == len(showers)):
        return {}
    sums: dict[str, list[float]] = {}
    for t, r, sh in zip(times, rain, showers):
        if not isinstance(t, str) or len(t) < 13 or t[10] != "T" or not t[11:13].isdigit():
            continue
        mm = (_num(r) or 0.0) + (_num(sh) or 0.0)
        half = 0 if int(t[11:13]) < 12 else 1
        sums.setdefault(t[:10], [0.0, 0.0])[half] += mm
    return {d: (v[0], v[1]) for d, v in sums.items()}


def parse_daily(data: object) -> list[dict]:
    """API 응답에서 일별 예보를 꺼낸다. 값이 빠진 날(코드나 기온이 null)은 건너뛴다.
    체감온도·바람·강수량·일출일몰은 없어도 된다. 없으면 그 값만 None 이다.

    반환: [{"date": "YYYY-MM-DD", "code": int, "tmin": float, "tmax": float, "precip": int | None,
            "feels_min": float | None, "feels_max": float | None,
            "wind": float | None,          # 일 최대 풍속(m/s)
            "rain_mm": float | None,       # 비 + 소나기 합계(mm). 눈은 뺀다
            "snow_cm": float | None,
            "sunrise": "HH:MM" | None, "sunset": "HH:MM" | None,
            "rain_am_mm": float | None,    # 0~11시 비(mm). 시간별 값이 없으면 None
            "rain_pm_mm": float | None}]   # 12~23시
    """
    try:
        daily = data["daily"]            # type: ignore[index]
        times = daily["time"]
        codes = daily["weather_code"]
        tmax = daily["temperature_2m_max"]
        tmin = daily["temperature_2m_min"]
        if not isinstance(times, list):
            raise ProviderError("bad_shape")
        n = len(times)
        precip = _optional_series(daily, "precipitation_probability_max", n)
        feels_max = _optional_series(daily, "apparent_temperature_max", n)
        feels_min = _optional_series(daily, "apparent_temperature_min", n)
        wind = _optional_series(daily, "wind_speed_10m_max", n)
        rain = _optional_series(daily, "rain_sum", n)
        showers = _optional_series(daily, "showers_sum", n)
        snow = _optional_series(daily, "snowfall_sum", n)
        sunrise = _optional_series(daily, "sunrise", n)
        sunset = _optional_series(daily, "sunset", n)
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("bad_shape") from None
    series = (times, codes, tmax, tmin, precip, feels_max, feels_min, wind, rain, showers, snow, sunrise, sunset)
    if not all(isinstance(x, list) and len(x) == n for x in series):
        raise ProviderError("bad_shape")

    halves = _rain_by_half_day(data)
    out: list[dict] = []
    for t, c, hi, lo, p, fhi, flo, w, r, sh, sn, sr, ss in zip(*series):
        try:
            date.fromisoformat(t)
            if c is None or hi is None or lo is None:
                continue
            code, hi_f, lo_f = int(c), float(hi), float(lo)
            p_i = None if p is None else max(0, min(100, int(round(float(p)))))
        except (TypeError, ValueError):
            continue
        rain_f, showers_f = _num(r), _num(sh)
        out.append({
            "date": t, "code": code, "tmin": lo_f, "tmax": hi_f, "precip": p_i,
            "feels_min": _num(flo), "feels_max": _num(fhi),
            "wind": _num(w),
            "rain_mm": None if rain_f is None and showers_f is None else (rain_f or 0.0) + (showers_f or 0.0),
            "snow_cm": _num(sn),
            "sunrise": _hhmm(sr), "sunset": _hhmm(ss),
            "rain_am_mm": round(halves[t][0], 2) if t in halves else None,
            "rain_pm_mm": round(halves[t][1], 2) if t in halves else None,
        })
    return out


async def fetch_daily(lat: float, lng: float) -> list[dict]:
    """한 지점의 일별 예보(지난 PAST_DAYS일 + 앞으로 FORECAST_DAYS일). 날짜는 그 위치의 현지 날짜."""
    params: dict[str, str | int | float] = {
        "latitude": round(lat, 4),
        "longitude": round(lng, 4),
        "daily": DAILY_FIELDS,
        "hourly": HOURLY_FIELDS,
        "timezone": "auto",
        "wind_speed_unit": "ms",      # 웹의 STRONG_WIND_THRESHOLD(8)가 m/s 기준이다. 기본값은 km/h 다.
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


def parse_history(data: object) -> list[dict]:
    """과거 기록(Archive API) 응답에서 일별 값을 꺼낸다. 기온이 null 인 날은 건너뛴다.

    반환: [{"date": "YYYY-MM-DD", "tmin": float, "tmax": float, "rain_mm": float | None}]
    """
    try:
        daily = data["daily"]            # type: ignore[index]
        times = daily["time"]
        tmax = daily["temperature_2m_max"]
        tmin = daily["temperature_2m_min"]
        rain = daily["rain_sum"]
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("bad_shape") from None
    series = (times, tmax, tmin, rain)
    if not all(isinstance(x, list) for x in series) or len({len(x) for x in series}) != 1:
        raise ProviderError("bad_shape")
    out: list[dict] = []
    for t, hi, lo, r in zip(*series):
        try:
            date.fromisoformat(t)
        except (TypeError, ValueError):
            continue
        hi_f, lo_f = _num(hi), _num(lo)
        if hi_f is None or lo_f is None:
            continue
        out.append({"date": t, "tmin": lo_f, "tmax": hi_f, "rain_mm": _num(r)})
    return out


async def fetch_history(lat: float, lng: float, start: date, end: date) -> list[dict]:
    """한 지점의 start ~ end(포함) 일별 기록. 평년 계산용이라 한 번에 10년치를 받는다.
    rain_sum 은 눈을 뺀 비의 합계(mm)다."""
    params: dict[str, str | float] = {
        "latitude": round(lat, 4),
        "longitude": round(lng, 4),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,rain_sum",
        "timezone": "auto",
    }
    try:
        r = await client.get(ARCHIVE_URL, params=params, timeout=HISTORY_TIMEOUT)
    except httpx.HTTPError:
        raise ProviderError("network") from None
    if r.status_code != 200:
        raise ProviderError(f"status_{r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise ProviderError("bad_json") from None
    return parse_history(data)


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
