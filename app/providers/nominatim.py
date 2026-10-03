import asyncio
import time

import httpx

from ..config import settings
from . import ProviderError

PROVIDER = "nominatim"
ATTRIBUTION = "© OpenStreetMap contributors"
URL = "https://nominatim.openstreetmap.org/search"

client = httpx.AsyncClient(timeout=10)   # main.py 의 lifespan 에서 종료

# 공용 서버 정책(초당 1회 이하)을 지키기 위해 모든 요청을 직렬화한다. (uvicorn 워커 1개 기준)
_lock = asyncio.Lock()
_last = 0.0


async def close() -> None:
    await client.aclose()


async def search(
    q: str,
    lang: str | None,
    lat: float | None,
    lng: float | None,
    half_span_deg: float | None,
    limit: int = 8,
) -> list[dict]:
    """전체 검색. 사용자가 검색을 확정했을 때만 호출할 것 (입력 중 자동완성 용도로 쓰지 않는다).
    lat/lng 와 half_span_deg 가 있으면 그 사각형 안으로 제한한다."""
    global _last
    params: dict[str, str | int] = {"q": q, "format": "jsonv2", "limit": limit}
    if lang:
        params["accept-language"] = lang
    if lat is not None and lng is not None and half_span_deg:
        d = half_span_deg
        params["viewbox"] = f"{lng - d},{lat + d},{lng + d},{lat - d}"   # left,top,right,bottom
        params["bounded"] = 1

    async with _lock:
        wait = 1.1 - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await client.get(URL, params=params, headers={"User-Agent": settings.nominatim_user_agent})
        except httpx.HTTPError:
            raise ProviderError("network") from None
        finally:
            _last = time.monotonic()
    if r.status_code != 200:
        raise ProviderError(f"status_{r.status_code}")

    items: list[dict] = []
    for x in r.json():
        try:
            plat, plon = float(x["lat"]), float(x["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        osm_type, osm_id = x.get("osm_type"), x.get("osm_id")
        if not osm_type or osm_id is None:
            continue
        display = x.get("display_name") or ""
        name = x.get("name") or display.split(",")[0].strip()
        items.append({
            "provider": PROVIDER,
            "provider_place_id": f"{osm_type}/{osm_id}",
            "name": name[:100],
            "address_text": display[:300] or None,
            "lat": plat,
            "lng": plon,
        })
    return items