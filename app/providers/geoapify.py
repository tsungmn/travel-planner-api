import httpx

from ..config import settings
from . import ProviderError

PROVIDER = "geoapify"
ATTRIBUTION = "Powered by Geoapify · © OpenStreetMap contributors"
URL = "https://api.geoapify.com/v1/geocode/autocomplete"

client = httpx.AsyncClient(timeout=8)   # main.py 의 lifespan 에서 종료


async def close() -> None:
    await client.aclose()


async def suggest(
    q: str,
    lang: str | None,
    lat: float | None,
    lng: float | None,
    radius_m: int | None,
    limit: int = 8,
) -> list[dict]:
    """입력 중 제안. lat/lng 가 있으면 가까운 결과를 우선하고, radius_m 이 있으면 그 반경 안으로 제한한다."""
    params: dict[str, str | int] = {"text": q, "limit": limit, "apiKey": settings.geoapify_api_key}
    if lang:
        params["lang"] = lang
    if lat is not None and lng is not None:
        params["bias"] = f"proximity:{lng},{lat}"                    # lon,lat 순서
        if radius_m:
            params["filter"] = f"circle:{lng},{lat},{radius_m}"

    try:
        r = await client.get(URL, params=params)
    except httpx.HTTPError:
        raise ProviderError("network") from None     # 예외 메시지에 API 키가 든 URL이 섞이지 않도록
    if r.status_code != 200:
        raise ProviderError(f"status_{r.status_code}")

    items: list[dict] = []
    for f in r.json().get("features", []):
        pr = f.get("properties") or {}
        plon, plat, pid = pr.get("lon"), pr.get("lat"), pr.get("place_id")
        if plon is None or plat is None or not pid:
            continue
        formatted = pr.get("formatted") or ""
        name = pr.get("name") or pr.get("address_line1") or formatted
        items.append({
            "provider": PROVIDER,
            "provider_place_id": str(pid),
            "name": name[:100],
            "address_text": formatted[:300] or None,
            "lat": plat,
            "lng": plon,
        })
    return items