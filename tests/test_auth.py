def test_register_verify_login_me_logout_cookie(client, outbox):
    r = client.post("/api/auth/register", json={"email": "A@Example.com", "password": "password123"})
    assert r.status_code == 201 and r.json() == {"email": "a@example.com"}    # 세션도 토큰도 주지 않는다
    assert client.get("/api/auth/me").status_code == 401                      # 가입만으로는 로그인되지 않음
    login = {"email": "a@example.com", "password": "password123"}
    assert client.post("/api/auth/login", json=login).status_code == 403      # 인증 전에는 로그인 불가
    assert client.post("/api/auth/verify-email", json={"token": outbox.token("a@example.com")}).status_code == 204
    assert client.post("/api/auth/login", json=login).status_code == 200      # 인증 후 로그인 (쿠키 발급)
    assert client.get("/api/auth/me").json()["email"] == "a@example.com"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_email_and_bad_password(client):
    body = {"email": "a@example.com", "password": "password123"}
    first = client.post("/api/auth/register", json=body)
    second = client.post("/api/auth/register", json=body)
    # 가입 여부를 알려 주지 않는다: 이미 있는 이메일도 새 이메일과 똑같이 응답한다.
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() == {"email": "a@example.com"}
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