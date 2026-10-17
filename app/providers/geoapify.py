import hashlib

import httpx

from ..config import settings
from . import ProviderError

PROVIDER = "geoapify"
ATTRIBUTION = "Powered by Geoapify · © OpenStreetMap contributors"
URL = "https://api.geoapify.com/v1/geocode/autocomplete"

MAX_PLACE_ID_LEN = 200   # schemas.ProviderPlaceId 의 max_length 와 같아야 한다

client = httpx.AsyncClient(timeout=8)   # main.py 의 lifespan 에서 종료


async def close() -> None:
    await client.aclose()


def _place_id(raw: object) -> str:
    """Geoapify place_id 는 장소 이름까지 인코딩해서 이름이 긴 장소는 상한을 넘는다.
    넘으면 해시로 줄인다. 같은 장소는 항상 같은 값이라 중복 확인에는 그대로 쓸 수 있고,
    이 값으로 Geoapify 에 다시 조회하는 곳은 없다."""
    pid = str(raw)
    if len(pid) <= MAX_PLACE_ID_LEN:
        return pid
    return "sha256:" + hashlib.sha256(pid.encode()).hexdigest()


async def suggest(
    q: str,
    lang: str | None,
    lat: float | None,
    lng: float | None,
    radius_m: int | None,
    limit: int = 8,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    """입력 중 제안. lat/lng 가 있으면 가까운 결과를 우선하고, radius_m 이 있으면 그 반경 안으로 제한한다.
    bbox(west, south, east, north)가 있으면 lat/lng/radius_m 대신 그 사각형 안으로 제한한다."""
    params: dict[str, str | int] = {"text": q, "limit": limit, "apiKey": settings.geoapify_api_key}
    if lang:
        params["lang"] = lang
    if bbox:
        west, south, east, north = bbox
        params["filter"] = f"rect:{west:.6f},{south:.6f},{east:.6f},{north:.6f}"   # lon1,lat1,lon2,lat2
        params["bias"] = f"proximity:{(west + east) / 2:.6f},{(south + north) / 2:.6f}"
    elif lat is not None and lng is not None:
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
    try:
        data = r.json()
        features = data.get("features", [])
    except (ValueError, AttributeError):
        raise ProviderError("bad_json") from None
    for f in features:
        pr = f.get("properties") or {}
        plon, plat, pid = pr.get("lon"), pr.get("lat"), pr.get("place_id")
        if plon is None or plat is None or not pid:
            continue
        formatted = pr.get("formatted") or ""
        name = (pr.get("name") or "").strip() or (pr.get("address_line1") or "").strip() or formatted.strip()
        if not name:
            continue    # 이름이 비면 장소 추가 시 422 (name 최소 1자) 가 되므로 결과에서 뺀다
        items.append({
            "provider": PROVIDER,
            "provider_place_id": _place_id(pid),
            "name": name[:100],
            "address_text": formatted[:300] or None,
            "lat": plat,
            "lng": plon,
        })
    return items