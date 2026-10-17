"""통합 테스트는 실제 PostgreSQL 이 필요하다. TEST_DATABASE_URL 이 없으면 전부 건너뛴다.

    TEST_DATABASE_URL=postgres://app:<비밀번호>@127.0.0.1:5432/travel_test pytest

DB 이름에 'test' 가 들어 있어야만 실행한다. (테스트 시작 시 public 스키마를 지우므로 개발/운영 DB 보호용)
"""
import asyncio
import os
from urllib.parse import urlparse

import asyncpg
import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")

if TEST_URL:
    if "test" not in urlparse(TEST_URL).path.lower():
        raise RuntimeError("TEST_DATABASE_URL 의 DB 이름에 'test' 가 들어 있어야 합니다")
    os.environ.update(
        APP_ENV="dev",
        DATABASE_URL=TEST_URL,
        SESSION_SECRET="test-secret-" + "x" * 32,
        PUBLIC_URL="http://localhost:3000",
        COOKIE_SECURE="false",
        DOCS_ENABLED="true",
        GOOGLE_CLIENT_ID="test",
        GOOGLE_CLIENT_SECRET="test",
        GEOAPIFY_API_KEY="",
    )


def pytest_collection_modifyitems(config, items):
    if TEST_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL 이 없어 통합 테스트를 건너뜁니다")
    for item in items:
        item.add_marker(skip)


def _run(sql: str) -> None:
    async def go():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()
    asyncio.run(go())


@pytest.fixture(scope="session", autouse=True)
def _schema():
    if not TEST_URL:
        yield
        return
    _run("drop schema public cascade; create schema public;")
    from app.migrate import main as migrate
    asyncio.run(migrate())
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app import main
    from app.routers import auth

    _run("truncate users, account_deletions restart identity cascade")
    for lim in (auth._login_fail_email, auth._login_fail_ip, auth._register_ip):
        lim._hits.clear()
    # 쿠키 상태가 섞이지 않게 사용자는 Bearer 헤더로만 구분한다. (한 클라이언트 = 한 이벤트 루프)
    with TestClient(main.app) as c:
        yield c


class Users:
    def __init__(self, c):
        self.c = c

    def signup(self, email: str, password: str = "password123") -> dict:
        r = self.c.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        self.c.cookies.clear()
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def trip(self, h: dict, **over) -> dict:
        body = {"title": "칭다오", "destination": "Qingdao", "start_date": "2026-11-01", "end_date": "2026-11-03"}
        r = self.c.post("/api/trips", json=body | over, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    def stop(self, h: dict, trip_id: str, day: int = 0, name: str = "잔교") -> dict:
        r = self.c.post(
            f"/api/trips/{trip_id}/stops",
            json={"day_index": day, "name": name, "lat": 36.06, "lng": 120.31},
            headers=h,
        )
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture()
def users(client):
    return Users(client)