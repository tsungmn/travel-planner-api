"""통합 테스트는 실제 PostgreSQL 이 필요하다. TEST_DATABASE_URL 이 없으면 전부 건너뛴다.
(DB 가 필요 없는 순수 함수 테스트는 모듈에 `pytestmark = pytest.mark.no_db` 를 달면 항상 돈다.)

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
else:
    # DB 없이 도는 순수 함수 테스트도 app.config 를 import 할 수 있게, 필수 설정에만 더미 값을 둔다.
    # (이미 지정된 값은 건드리지 않고, 이 값으로는 어디에도 접속하지 않는다)
    for _k, _v in dict(
        DATABASE_URL="postgres://x:x@localhost:5432/unused",
        SESSION_SECRET="unit-test-secret-" + "x" * 32,
        PUBLIC_URL="http://localhost:3000",
        GOOGLE_CLIENT_ID="unused",
        GOOGLE_CLIENT_SECRET="unused",
    ).items():
        os.environ.setdefault(_k, _v)


def pytest_configure(config):
    config.addinivalue_line("markers", "no_db: DB 없이 도는 순수 함수 테스트")


def pytest_collection_modifyitems(config, items):
    if TEST_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL 이 없어 통합 테스트를 건너뜁니다")
    for item in items:
        if item.get_closest_marker("no_db") is None:
            item.add_marker(skip)


def _run(sql: str, *args) -> None:
    async def go():
        conn = await asyncpg.connect(TEST_URL)
        try:
            await conn.execute(sql, *args)
        finally:
            await conn.close()
    asyncio.run(go())


def _fetch(sql: str, *args) -> list:
    async def go():
        conn = await asyncpg.connect(TEST_URL)
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()
    return asyncio.run(go())


@pytest.fixture(scope="session", autouse=True)
def _schema():
    if not TEST_URL:
        yield
        return
    _run("drop schema public cascade; create schema public;")
    from app.migrate import main as migrate
    asyncio.run(migrate())
    yield


class Outbox(list):
    """테스트 중에 '발송된' 메일을 모아 둔다. 실제 SMTP 로는 아무것도 나가지 않는다."""

    def token(self, to: str, which: str = "verify-email") -> str:
        """to 에게 온 가장 최근 메일에서 링크의 토큰을 꺼낸다."""
        import re

        mails = [m for m in self if m["to"] == to]
        assert mails, f"{to} 에게 온 메일이 없습니다"
        found = re.search(rf"/{which}\?token=([A-Za-z0-9_-]+)", mails[-1]["text"])
        assert found, mails[-1]["text"]
        return found.group(1)


@pytest.fixture()
def outbox(monkeypatch):
    from app import mailer

    box = Outbox()

    async def fake_send(to, subject, text, html_body):
        box.append({"to": to, "subject": subject, "text": text, "html": html_body})
        return True

    monkeypatch.setattr(mailer, "send_email", fake_send)
    return box


@pytest.fixture()
def client(outbox):
    from fastapi.testclient import TestClient

    from app import main
    from app.ratelimit import AttemptLimiter
    from app.routers import auth

    _run("truncate users, account_deletions restart identity cascade")
    for lim in vars(auth).values():     # 속도 제한은 모듈 전역이라 테스트마다 비운다
        if isinstance(lim, AttemptLimiter):
            lim._hits.clear()
    # 쿠키 상태가 섞이지 않게 사용자는 Bearer 헤더로만 구분한다. (한 클라이언트 = 한 이벤트 루프)
    with TestClient(main.app) as c:
        yield c


class Users:
    def __init__(self, c):
        self.c = c

    def signup(self, email: str, password: str = "password123") -> dict:
        """가입하고 이메일 인증까지 끝낸 뒤 로그인해서 Bearer 헤더를 돌려준다."""
        r = self.c.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        _run("update users set email_verified_at = now() where email = $1", email.lower())
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
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