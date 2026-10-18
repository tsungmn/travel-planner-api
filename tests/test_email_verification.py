"""이메일 인증, 인증 메일 재전송, 비밀번호 재설정, Google 연결 시의 인증 처리."""
import pytest
from conftest import _fetch, _run

PW = "password123"
NEW_PW = "brand-new-pw-1"


def register(client, email="a@example.com", password=PW):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r


def login(client, email="a@example.com", password=PW):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    client.cookies.clear()
    return r


# ---------------------------------------------------------------- 가입과 인증

def test_register_sends_one_mail_and_creates_no_session(client, outbox):
    r = register(client)
    assert r.json() == {"email": "a@example.com"}
    assert "sid" not in client.cookies
    assert len(outbox) == 1 and outbox[0]["to"] == "a@example.com"
    # 링크는 웹 주소(PUBLIC_URL)를 가리킨다. 텍스트와 HTML 둘 다 들어 있어야 한다.
    assert "http://localhost:3000/verify-email?token=" in outbox[0]["text"]
    assert "http://localhost:3000/verify-email?token=" in outbox[0]["html"]


def test_unverified_login_is_403_only_with_the_right_password(client):
    register(client)
    assert login(client).status_code == 403
    assert login(client).json() == {"detail": "email_not_verified"}
    # 틀린 비밀번호로는 계정이 인증 전인지 알 수 없어야 한다
    wrong = login(client, password="nope-nope-1")
    ghost = login(client, email="ghost@example.com", password="nope-nope-1")
    assert wrong.status_code == ghost.status_code == 401
    assert wrong.json() == ghost.json() == {"detail": "invalid_credentials"}


def test_verify_token_works_once_and_garbage_is_rejected(client, outbox):
    register(client)
    token = outbox.token("a@example.com")
    for bad in ("garbage", ""):
        r = client.post("/api/auth/verify-email", json={"token": bad})
        assert r.status_code == 400 and r.json() == {"detail": "invalid_or_expired_token"}
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 204
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400   # 한 번만
    assert login(client).status_code == 200


def test_expired_verify_token_is_rejected(client, outbox):
    register(client)
    _run("update email_tokens set expires_at = now() - interval '1 minute'")
    r = client.post("/api/auth/verify-email", json={"token": outbox.token("a@example.com")})
    assert r.status_code == 400
    assert login(client).status_code == 403


def test_token_is_stored_only_as_hash(client, outbox):
    register(client)
    token = outbox.token("a@example.com")
    rows = _fetch("select token_hash, purpose from email_tokens")
    assert len(rows) == 1 and rows[0]["purpose"] == "verify_email"
    assert token not in rows[0]["token_hash"] and len(rows[0]["token_hash"]) == 64


def test_verify_endpoint_is_rate_limited_per_ip(client):
    codes = [client.post("/api/auth/verify-email", json={"token": "x"}).status_code for _ in range(21)]
    assert codes[:20] == [400] * 20 and codes[20] == 429


def test_duplicate_registration_keeps_one_mail(client, outbox):
    register(client)
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": PW})
    assert r.status_code == 409
    assert len(outbox) == 1


# ---------------------------------------------------------------- 인증 메일 재전송

def test_resend_replaces_the_old_link(client, outbox):
    register(client)
    old = outbox.token("a@example.com")
    assert client.post("/api/auth/resend-verification", json={"email": "A@Example.com"}).status_code == 204
    assert len(outbox) == 2
    new = outbox.token("a@example.com")
    assert new != old
    assert client.post("/api/auth/verify-email", json={"token": old}).status_code == 400
    assert client.post("/api/auth/verify-email", json={"token": new}).status_code == 204


def test_resend_says_nothing_about_unknown_or_verified_accounts(client, outbox, users):
    users.signup("done@example.com")
    sent = len(outbox)
    for email in ("ghost@example.com", "done@example.com"):
        r = client.post("/api/auth/resend-verification", json={"email": email})
        assert r.status_code == 204 and r.content == b""
    assert len(outbox) == sent          # 아무 메일도 나가지 않았다


def test_resend_limit_counts_unknown_emails_too(client, outbox):
    codes = [
        client.post("/api/auth/resend-verification", json={"email": "ghost@example.com"}).status_code
        for _ in range(6)
    ]
    assert codes == [204] * 5 + [429]
    assert outbox == []


# ---------------------------------------------------------------- 비밀번호 재설정

def test_forgot_password_unknown_email_is_silent(client, outbox):
    r = client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert r.status_code == 204 and outbox == []


def test_reset_password_full_flow_and_revokes_sessions(client, outbox, users):
    h = users.signup("a@example.com")                 # 가입 때 인증 메일이 이미 한 통 나갔다
    assert client.get("/api/auth/me", headers=h).status_code == 200
    before = len(outbox)

    assert client.post("/api/auth/forgot-password", json={"email": "a@example.com"}).status_code == 204
    assert len(outbox) == before + 1
    assert "http://localhost:3000/reset-password?token=" in outbox[-1]["text"]
    token = outbox.token("a@example.com", "reset-password")

    r = client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PW})
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401           # 기존 로그인은 끊긴다
    assert login(client, password=PW).status_code == 401                      # 옛 비밀번호 불가
    assert login(client, password=NEW_PW).status_code == 200
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PW})
    assert r.status_code == 400 and r.json() == {"detail": "invalid_or_expired_token"}   # 한 번만


def test_reset_with_short_password_does_not_burn_the_token(client, outbox):
    register(client)
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    token = outbox.token("a@example.com", "reset-password")
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "short"})
    assert r.status_code == 422 and r.json()["detail"] == "validation_error"
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PW}).status_code == 204


def test_reset_also_verifies_an_unverified_account(client, outbox):
    register(client)
    assert login(client).status_code == 403
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    token = outbox.token("a@example.com", "reset-password")
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PW}).status_code == 204
    assert login(client, password=NEW_PW).status_code == 200                  # 인증 메일 없이도 로그인된다


def test_new_reset_link_replaces_the_old_one(client, outbox):
    register(client)
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    old = outbox.token("a@example.com", "reset-password")
    client.post("/api/auth/forgot-password", json={"email": "a@example.com"})
    new = outbox.token("a@example.com", "reset-password")
    assert client.post("/api/auth/reset-password", json={"token": old, "password": NEW_PW}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": new, "password": NEW_PW}).status_code == 204


def test_verify_link_cannot_be_used_as_reset_link(client, outbox):
    register(client)
    token = outbox.token("a@example.com")           # 인증용 토큰
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PW})
    assert r.status_code == 400


def test_google_only_account_can_set_a_password_by_reset(client, outbox):
    _run(
        "insert into users (email, google_sub, email_verified_at) values ('g@example.com', 'sub-1', now())"
    )
    assert login(client, "g@example.com", PW).status_code == 401              # 비밀번호가 아직 없다
    client.post("/api/auth/forgot-password", json={"email": "g@example.com"})
    token = outbox.token("g@example.com", "reset-password")
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PW}).status_code == 204
    assert login(client, "g@example.com", NEW_PW).status_code == 200


# ---------------------------------------------------------------- Google 로그인과의 관계

@pytest.fixture()
def google(monkeypatch):
    """Google 서버에 가지 않고 콜백이 받을 사용자 정보를 바꿔 끼운다."""
    from app.routers import auth

    info = {}

    async def fake_token(request):
        return {"userinfo": dict(info)}

    monkeypatch.setattr(auth.oauth.google, "authorize_access_token", fake_token)
    return info


def test_new_google_account_is_verified_and_logged_in(client, google):
    google.update(email="g@example.com", email_verified=True, sub="sub-1", name="G")
    r = client.get("/api/auth/google/callback", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].endswith("/trips")
    assert client.get("/api/auth/me").json()["email"] == "g@example.com"
    assert _fetch("select email_verified_at from users")[0]["email_verified_at"] is not None


def test_google_login_takes_over_an_unverified_password_account(client, outbox, google):
    register(client)                                  # 이메일을 선점한 가입 (아직 인증 안 함)
    assert len(_fetch("select 1 from email_tokens")) == 1
    google.update(email="a@example.com", email_verified=True, sub="sub-1", name="Real Owner")
    assert client.get("/api/auth/google/callback", follow_redirects=False).status_code in (302, 307)
    row = _fetch("select password_hash, google_sub, email_verified_at from users")[0]
    assert row["password_hash"] is None and row["google_sub"] == "sub-1"
    assert row["email_verified_at"] is not None
    assert _fetch("select 1 from email_tokens") == []                           # 선점자의 메일 링크도 폐기
    assert login(client, password=PW).status_code == 401                        # 선점자의 비밀번호는 못 쓴다
