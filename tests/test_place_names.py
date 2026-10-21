import asyncio
import functools

import httpx
import pytest

from app.place_names import PlaceNameService
from app.providers import ProviderError, geoapify
from app.weather import grid_center, grid_key

pytestmark = pytest.mark.no_db

PRAGUE = (50.0755, 14.4378)
VIENNA = (48.2082, 16.3738)


def aio(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ----- 응답 해석 -----
def test_parse_city_prefers_city_then_smaller_to_larger_units():
    assert geoapify.parse_city({"results": [{"city": "프라하", "state": "프라하"}]}) == "프라하"
    assert geoapify.parse_city({"results": [{"town": "체스키크룸로프", "county": "X"}]}) == "체스키크룸로프"
    assert geoapify.parse_city({"results": [{"county": "Y", "state": "Z"}]}) == "Y"
    assert geoapify.parse_city({"results": [{"city": "  "}, {"village": "V"}]}) == "V"
    assert geoapify.parse_city({"results": [{"country": "체코"}]}) is None      # 도시급 필드가 없다
    assert geoapify.parse_city({"results": []}) is None
    assert len(geoapify.parse_city({"results": [{"city": "가" * 300}]})) == 100


def test_parse_city_bad_shapes():
    for bad in (None, {}, {"results": "x"}, []):
        with pytest.raises(ProviderError):
            geoapify.parse_city(bad)


@aio
async def test_reverse_city_request_and_errors(monkeypatch):
    seen = {}

    def ok(req):
        seen["q"] = dict(req.url.params)
        return httpx.Response(200, json={"results": [{"city": "Prague"}]})

    monkeypatch.setattr(geoapify, "client", httpx.AsyncClient(transport=httpx.MockTransport(ok)))
    assert await geoapify.reverse_city(50.1, 14.4) == "Prague"
    assert seen["q"]["lang"] == "ko" and seen["q"]["format"] == "json"
    assert seen["q"]["lat"] == "50.1" and seen["q"]["lon"] == "14.4"

    for resp, reason in ((httpx.Response(401), "status_401"), (httpx.Response(200, text="<html>"), "bad_json")):
        monkeypatch.setattr(geoapify, "client",
                            httpx.AsyncClient(transport=httpx.MockTransport(lambda r, resp=resp: resp)))
        with pytest.raises(ProviderError, match=reason):
            await geoapify.reverse_city(50.1, 14.4)

    def boom(req):
        raise httpx.ConnectError("x", request=req)
    monkeypatch.setattr(geoapify, "client", httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(ProviderError, match="network") as e:
        await geoapify.reverse_city(50.1, 14.4)
    assert "apiKey" not in str(e.value)


# ----- 캐시 -----
class MemStore:
    def __init__(self):
        self.rows, self.saves = {}, 0

    async def load(self, keys):
        return {k: self.rows[k] for k in keys if k in self.rows}

    async def save(self, key, name):
        self.rows[key] = name
        self.saves += 1


def make(store=None, lookup=None, enabled=True):
    state = {"mono": 0.0, "calls": []}

    async def default(lat, lng):
        state["calls"].append((lat, lng))
        return "프라하" if lat > 49 else "빈"

    svc = PlaceNameService(store or MemStore(), lookup or default, enabled=lambda: enabled,
                           mono=lambda: state["mono"])
    return svc, state


@aio
async def test_looks_up_once_per_grid_and_keeps_forever():
    svc, st = make()
    near = (50.0801, 14.4402)      # PRAGUE 와 같은 격자
    r = await svc.names([PRAGUE, near, VIENNA])
    assert r == {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): "빈"} and len(st["calls"]) == 2
    st["mono"] += 10 ** 9
    await svc.names([PRAGUE])
    assert len(st["calls"]) == 2                    # 저장돼 있으면 다시 조회하지 않는다
    assert st["calls"][0] == grid_center(grid_key(*PRAGUE))


@aio
async def test_failure_gives_none_and_backs_off():
    n = 0

    async def failing(lat, lng):
        nonlocal n
        n += 1
        raise ProviderError("status_429")

    svc, st = make(lookup=failing)
    assert (await svc.names([PRAGUE]))[grid_key(*PRAGUE)] is None
    await svc.names([PRAGUE])
    assert n == 1
    st["mono"] += 61
    await svc.names([PRAGUE])
    assert n == 2


@aio
async def test_not_found_waits_longer_and_is_not_saved():
    async def nothing(lat, lng):
        return None

    store = MemStore()
    svc, st = make(store, nothing)
    assert (await svc.names([PRAGUE]))[grid_key(*PRAGUE)] is None
    assert store.saves == 0
    st["mono"] += 61
    calls = []

    async def spy(lat, lng):
        calls.append(1)
        return "x"
    svc._lookup = spy
    await svc.names([PRAGUE])
    assert calls == []             # 1분 뒤에는 다시 조회하지 않는다
    st["mono"] += 3600
    await svc.names([PRAGUE])
    assert calls == [1]


@aio
async def test_disabled_without_api_key_makes_no_requests_but_serves_stored():
    store = MemStore()
    store.rows[grid_key(*PRAGUE)] = "프라하"
    svc, st = make(store, enabled=False)
    r = await svc.names([PRAGUE, VIENNA])
    assert r == {grid_key(*PRAGUE): "프라하", grid_key(*VIENNA): None} and st["calls"] == []


@aio
async def test_concurrent_requests_share_one_lookup_and_broken_store_is_tolerated():
    gate, n = asyncio.Event(), 0

    async def slow(lat, lng):
        nonlocal n
        n += 1
        await gate.wait()
        return "프라하"

    class Broken(MemStore):
        async def load(self, keys):
            raise RuntimeError("db down")

        async def save(self, key, name):
            raise RuntimeError("db down")

    svc, _ = make(Broken(), slow)
    tasks = [asyncio.create_task(svc.names([PRAGUE])) for _ in range(3)]
    await asyncio.sleep(0.01)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert n == 1 and all(r[grid_key(*PRAGUE)] == "프라하" for r in results)
