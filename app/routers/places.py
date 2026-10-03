import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..db import get_pool
from ..deps import current_user

router = APIRouter(prefix="/api/places", tags=["places"])

AUTOCOMPLETE_LIMIT = 300
DETAILS_LIMIT = 150
LANGS = {"ko", "en", "zh-CN", "ja"}

http_client = httpx.AsyncClient(timeout=8)   # main.py 의 lifespan 에서 종료


async def _bump_usage(user_id, kind: str, limit: int) -> bool:
    n = await get_pool().fetchval(
        """insert into place_usage (user_id, day, kind, n) values ($1, current_date, $2, 1)
           on conflict (user_id, day, kind) do update set n = place_usage.n + 1
           returning n""",
        user_id, kind,
    )
    return n <= limit


@router.get("/autocomplete")
async def autocomplete(
    q: str = Query(min_length=2, max_length=100),
    token: str = Query(min_length=8, max_length=100),
    lang: str = "ko",
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    user: dict = Depends(current_user),
):
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "bad_request")
    if not await _bump_usage(user["id"], "autocomplete", AUTOCOMPLETE_LIMIT):
        raise HTTPException(429, "rate_limited")

    body: dict = {"input": q, "sessionToken": token, "languageCode": lang if lang in LANGS else "ko"}
    if lat is not None and lng is not None:
        body["locationBias"] = {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": 50000.0}
        }

    r = await http_client.post(
        "https://places.googleapis.com/v1/places:autocomplete",
        json=body,
        headers={"X-Goog-Api-Key": settings.google_places_server_key},
    )
    if r.status_code != 200:
        raise HTTPException(502, "upstream_error")

    items = []
    for s in r.json().get("suggestions", []):
        p = s.get("placePrediction")
        if not p:
            continue
        fmt = p.get("structuredFormat", {})
        items.append({
            "placeId": p["placeId"],
            "main": fmt.get("mainText", {}).get("text") or p.get("text", {}).get("text", ""),
            "secondary": fmt.get("secondaryText", {}).get("text", ""),
        })
    return {"items": items}


@router.get("/details")
async def details(
    placeId: str = Query(pattern=r"^[A-Za-z0-9_-]{10,300}$"),
    token: str = Query(min_length=8, max_length=100),
    lang: str = "ko",
    user: dict = Depends(current_user),
):
    if not await _bump_usage(user["id"], "details", DETAILS_LIMIT):
        raise HTTPException(429, "rate_limited")

    # displayName은 Pro 등급 필드라 이 호출로 세션을 마치면 자동완성 요청이 무료 처리된다.
    # 필드를 늘리면 과금 등급이 올라가니 이 목록을 유지할 것.
    r = await http_client.get(
        f"https://places.googleapis.com/v1/places/{placeId}",
        params={"sessionToken": token, "languageCode": lang if lang in LANGS else "ko"},
        headers={
            "X-Goog-Api-Key": settings.google_places_server_key,
            "X-Goog-FieldMask": "id,displayName,formattedAddress,location",
        },
    )
    if r.status_code != 200:
        raise HTTPException(502, "upstream_error")

    p = r.json()
    return {
        "placeId": p.get("id"),
        "name": p.get("displayName", {}).get("text", ""),
        "address": p.get("formattedAddress", ""),
        "lat": p.get("location", {}).get("latitude"),
        "lng": p.get("location", {}).get("longitude"),
    }