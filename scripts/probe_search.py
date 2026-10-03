"""장소 검색 공급자 비교용 (서비스 코드와 무관).
사용: python scripts/probe_search.py <maptiler|geoapify|photon|nominatim>
환경 변수: MAPTILER_API_KEY / GEOAPIFY_API_KEY  (photon, nominatim 은 키 없이 사용)"""
import os
import sys
import time
from urllib.parse import quote

import httpx

LON, LAT = 120.38, 36.07   # 칭다오
QUERIES = [
    "5·4광장", "五四广场", "올림픽 요트센터", "奥帆中心", "완샹청", "万象城",
    "성 미카엘 대성당", "圣弥厄尔大教堂", "중산로", "中山路", "잔교", "栈桥",
    "영빈관", "迎宾馆", "신호산", "信号山", "타이동", "台东",
    "青岛啤酒博物馆", "劈柴院", "春和楼", "八大关", "小鱼山", "海底捞 青岛",
]


def need(name: str) -> str:
    return os.environ.get(name) or sys.exit(f"{name} 환경 변수가 필요합니다")


def maptiler(q, lang):
    p = {"key": need("MAPTILER_API_KEY"), "limit": 3, "autocomplete": "true", "proximity": f"{LON},{LAT}"}
    if lang:
        p["language"] = lang
    r = httpx.get(f"https://api.maptiler.com/geocoding/{quote(q, safe='')}.json", params=p, timeout=10)
    data = r.json() if r.status_code == 200 else {}
    return r.status_code, [(f.get("place_name"), f.get("center")) for f in data.get("features", [])[:3]]


def geoapify(q, lang):
    p = {"text": q, "limit": 3, "bias": f"proximity:{LON},{LAT}", "apiKey": need("GEOAPIFY_API_KEY")}
    if lang:
        p["lang"] = lang
    r = httpx.get("https://api.geoapify.com/v1/geocode/autocomplete", params=p, timeout=10)
    data = r.json() if r.status_code == 200 else {}
    rows = []
    for f in data.get("features", [])[:3]:
        pr = f.get("properties", {})
        rows.append((pr.get("formatted"), [pr.get("lon"), pr.get("lat")]))
    return r.status_code, rows


def photon(q, lang):
    # 언어 파라미터 지원 범위가 제한적이라 지정하지 않고 기본값만 확인
    r = httpx.get("https://photon.komoot.io/api/", params={"q": q, "limit": 3, "lat": LAT, "lon": LON}, timeout=10)
    data = r.json() if r.status_code == 200 else {}
    rows = []
    for f in data.get("features", [])[:3]:
        pr = f.get("properties", {})
        label = ", ".join(str(pr[k]) for k in ("name", "street", "city", "state", "country") if pr.get(k))
        rows.append((label, f.get("geometry", {}).get("coordinates")))
    return r.status_code, rows


def nominatim(q, lang):
    time.sleep(1.1)   # 공용 서버 이용 정책: 초당 1회 이하, User-Agent 필수
    p = {"q": q, "format": "jsonv2", "limit": 3, "viewbox": "120.2,36.25,120.6,35.9"}
    if lang:
        p["accept-language"] = lang
    r = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params=p, headers={"User-Agent": "travel-planner-probe/0.1"}, timeout=10,
    )
    data = r.json() if r.status_code == 200 else []
    return r.status_code, [(x.get("display_name"), [x.get("lon"), x.get("lat")]) for x in data[:3]]


PROVIDERS = {"maptiler": maptiler, "geoapify": geoapify, "photon": photon, "nominatim": nominatim}

name = sys.argv[1] if len(sys.argv) > 1 else ""
fn = PROVIDERS.get(name) or sys.exit(f"사용법: python scripts/probe_search.py <{'|'.join(PROVIDERS)}>")

for q in QUERIES:
    for lang in (None, "ko"):
        if name == "photon" and lang:
            continue
        status, rows = fn(q, lang)
        print(f"\n[{q}] lang={lang} status={status}")
        for label, center in rows:
            print("  -", label, center)