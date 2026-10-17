def test_register_login_me_logout_cookie(client):
    r = client.post("/api/auth/register", json={"email": "A@Example.com", "password": "password123"})
    assert r.status_code == 201 and r.json()["email"] == "a@example.com"
    assert client.get("/api/auth/me").json()["email"] == "a@example.com"      # 가입 쿠키로 인증됨
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_email_and_bad_password(client):
    body = {"email": "a@example.com", "password": "password123"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    assert client.post("/api/auth/register", json=body).status_code == 409
    r = client.post("/api/auth/register", json={"email": "b@example.com", "password": "short"})
    assert r.status_code == 422 and r.json()["detail"] == "validation_error"


def test_login_wrong_password_and_unknown_user_look_the_same(client, users):
    users.signup("a@example.com")
    wrong = client.post("/api/auth/login", json={"email": "a@example.com", "password": "nope-nope-1"})
    ghost = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-1"})
    assert wrong.status_code == ghost.status_code == 401
    assert wrong.json() == ghost.json() == {"detail": "invalid_credentials"}


def test_bearer_logout_revokes_session(client, users):
    h = users.signup("a@example.com")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_login_is_rate_limited_per_account(client, users):
    users.signup("a@example.com")
    bad = {"email": "a@example.com", "password": "nope-nope-1"}
    codes = [client.post("/api/auth/login", json=bad).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # 잠긴 동안은 올바른 비밀번호도 거부, 다른 계정은 영향 없음
    ok = {"email": "a@example.com", "password": "password123"}
    assert client.post("/api/auth/login", json=ok).status_code == 429
    users.signup("b@example.com")
    assert client.post("/api/auth/login", json={"email": "b@example.com", "password": "password123"}).status_code == 200


def test_delete_account_removes_data_and_keeps_only_anonymous_reason(client, users):
    h = users.signup("a@example.com")
    users.trip(h)
    r = client.request("DELETE", "/api/auth/me", json={"reason": "other", "detail": " bye "}, headers=h)
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401
    # 같은 이메일로 다시 가입할 수 있고, 여행은 비어 있다
    h2 = users.signup("a@example.com")
    assert client.get("/api/trips", headers=h2).json() == []